package com.library.log;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.library.common.BusinessException;
import com.library.entity.OperationLog;
import com.library.mapper.OperationLogMapper;
import com.library.security.AuthContext;
import com.library.security.UserSession;
import org.aspectj.lang.ProceedingJoinPoint;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class OperationLogAspectTest {

    @Mock
    private OperationLogMapper operationLogMapper;

    @AfterEach
    void clearRequest() {
        RequestContextHolder.resetRequestAttributes();
    }

    @Test
    @DisplayName("操作日志：成功操作记录操作人、IP、耗时与结果")
    void around_recordsSuccessfulOperation() throws Throwable {
        OperationLogAspect aspect = new OperationLogAspect(operationLogMapper, new ObjectMapper());
        ProceedingJoinPoint joinPoint = mock(ProceedingJoinPoint.class);
        when(joinPoint.proceed()).thenReturn("ok");
        when(joinPoint.getArgs()).thenReturn(new Object[]{Map.of("bookId", 1)});
        prepareRequest();

        Object result = aspect.around(joinPoint, auditable("借阅", "借阅图书"));

        assertThat(result).isEqualTo("ok");
        ArgumentCaptor<OperationLog> captor = ArgumentCaptor.forClass(OperationLog.class);
        verify(operationLogMapper).insert(captor.capture());
        OperationLog saved = captor.getValue();
        assertThat(saved.getModule()).isEqualTo("借阅");
        assertThat(saved.getAction()).isEqualTo("借阅图书");
        assertThat(saved.getStatus()).isEqualTo("SUCCESS");
        assertThat(saved.getUsername()).isEqualTo("admin");
        assertThat(saved.getIp()).isEqualTo("10.1.2.3");
        assertThat(saved.getUri()).isEqualTo("/api/borrows");
        assertThat(saved.getDurationMs()).isNotNull();
        assertThat(saved.getParams()).contains("bookId");
    }

    @Test
    @DisplayName("操作日志：密码字段写入前脱敏")
    void around_masksPasswordField() throws Throwable {
        OperationLogAspect aspect = new OperationLogAspect(operationLogMapper, new ObjectMapper());
        ProceedingJoinPoint joinPoint = mock(ProceedingJoinPoint.class);
        when(joinPoint.proceed()).thenReturn(null);
        when(joinPoint.getArgs()).thenReturn(new Object[]{Map.of("password", "secret123")});
        prepareRequest();

        aspect.around(joinPoint, auditable("用户", "重置密码"));

        ArgumentCaptor<OperationLog> captor = ArgumentCaptor.forClass(OperationLog.class);
        verify(operationLogMapper).insert(captor.capture());
        assertThat(captor.getValue().getParams())
                .contains("\"password\":\"***\"")
                .doesNotContain("secret123");
    }

    @Test
    @DisplayName("操作日志：失败操作记录错误信息并继续抛出异常")
    void around_recordsFailureAndRethrows() throws Throwable {
        OperationLogAspect aspect = new OperationLogAspect(operationLogMapper, new ObjectMapper());
        ProceedingJoinPoint joinPoint = mock(ProceedingJoinPoint.class);
        when(joinPoint.proceed()).thenThrow(new BusinessException("该记录没有待缴纳的罚金"));
        when(joinPoint.getArgs()).thenReturn(new Object[0]);
        prepareRequest();

        assertThatThrownBy(() -> aspect.around(joinPoint, auditable("借阅", "罚金缴纳")))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("没有待缴纳的罚金");

        ArgumentCaptor<OperationLog> captor = ArgumentCaptor.forClass(OperationLog.class);
        verify(operationLogMapper).insert(captor.capture());
        assertThat(captor.getValue().getStatus()).isEqualTo("FAIL");
        assertThat(captor.getValue().getErrorMessage()).contains("没有待缴纳的罚金");
    }

    @Test
    @DisplayName("操作日志：没有请求上下文时也能写入（不抛异常）")
    void around_withoutRequestContext_stillRecords() throws Throwable {
        OperationLogAspect aspect = new OperationLogAspect(operationLogMapper, new ObjectMapper());
        ProceedingJoinPoint joinPoint = mock(ProceedingJoinPoint.class);
        when(joinPoint.proceed()).thenReturn(null);
        when(joinPoint.getArgs()).thenReturn(new Object[0]);

        aspect.around(joinPoint, auditable("系统", "定时任务"));

        ArgumentCaptor<OperationLog> captor = ArgumentCaptor.forClass(OperationLog.class);
        verify(operationLogMapper).insert(captor.capture());
        assertThat(captor.getValue().getMethod()).isNull();
        assertThat(captor.getValue().getStatus()).isEqualTo("SUCCESS");
    }

    private void prepareRequest() {
        MockHttpServletRequest request = new MockHttpServletRequest("POST", "/api/borrows");
        request.addHeader("X-Forwarded-For", "10.1.2.3, 10.9.9.9");
        request.setAttribute(
                AuthContext.CURRENT_USER_ATTRIBUTE,
                new UserSession(1L, "admin", "系统管理员", "ADMIN"));
        RequestContextHolder.setRequestAttributes(new ServletRequestAttributes(request));
    }

    private Auditable auditable(String module, String action) {
        Auditable auditable = mock(Auditable.class);
        when(auditable.module()).thenReturn(module);
        when(auditable.action()).thenReturn(action);
        return auditable;
    }
}
