package com.library.log;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.library.entity.OperationLog;
import com.library.mapper.OperationLogMapper;
import com.library.security.AuthContext;
import com.library.security.UserSession;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.aspectj.lang.ProceedingJoinPoint;
import org.aspectj.lang.annotation.Around;
import org.aspectj.lang.annotation.Aspect;
import org.springframework.stereotype.Component;
import org.springframework.web.context.request.RequestContextHolder;
import org.springframework.web.context.request.ServletRequestAttributes;

import java.util.ArrayList;
import java.util.List;
import java.util.regex.Pattern;

@Slf4j
@Aspect
@Component
@RequiredArgsConstructor
public class OperationLogAspect {

    private static final int MAX_PARAM_LENGTH = 900;
    private static final int MAX_ERROR_LENGTH = 400;
    private static final Pattern SENSITIVE_FIELD =
            Pattern.compile("(\"(?:password|newPassword)\"\\s*:\\s*)\"[^\"]*\"");

    private final OperationLogMapper operationLogMapper;
    private final ObjectMapper objectMapper;

    @Around("@annotation(auditable)")
    public Object around(ProceedingJoinPoint joinPoint, Auditable auditable) throws Throwable {
        long startedAt = System.currentTimeMillis();
        Throwable failure = null;
        try {
            return joinPoint.proceed();
        } catch (Throwable ex) {
            failure = ex;
            throw ex;
        } finally {
            try {
                record(joinPoint, auditable, failure, System.currentTimeMillis() - startedAt);
            } catch (Exception ex) {
                log.warn("操作日志写入失败：{}", ex.getMessage());
            }
        }
    }

    private void record(ProceedingJoinPoint joinPoint, Auditable auditable, Throwable failure, long durationMs) {
        HttpServletRequest request = currentRequest();
        OperationLog entity = new OperationLog();
        entity.setModule(auditable.module());
        entity.setAction(auditable.action());
        entity.setMethod(request == null ? null : request.getMethod());
        entity.setUri(request == null ? null : request.getRequestURI());
        entity.setIp(resolveIp(request));
        entity.setParams(serializeArgs(joinPoint.getArgs()));
        entity.setStatus(failure == null ? "SUCCESS" : "FAIL");
        entity.setErrorMessage(failure == null ? null : truncate(failure.getMessage(), MAX_ERROR_LENGTH));
        entity.setDurationMs(durationMs);
        UserSession user = currentUser(request);
        if (user != null) {
            entity.setUserId(user.getId());
            entity.setUsername(user.getUsername());
        }
        operationLogMapper.insert(entity);
    }

    private HttpServletRequest currentRequest() {
        if (RequestContextHolder.getRequestAttributes() instanceof ServletRequestAttributes attributes) {
            return attributes.getRequest();
        }
        return null;
    }

    private UserSession currentUser(HttpServletRequest request) {
        if (request == null) {
            return null;
        }
        Object user = request.getAttribute(AuthContext.CURRENT_USER_ATTRIBUTE);
        return user instanceof UserSession session ? session : null;
    }

    private String resolveIp(HttpServletRequest request) {
        if (request == null) {
            return null;
        }
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            return truncate(forwarded.split(",")[0].trim(), 64);
        }
        return truncate(request.getRemoteAddr(), 64);
    }

    private String serializeArgs(Object[] args) {
        List<String> parts = new ArrayList<>();
        for (Object arg : args) {
            if (arg == null
                    || arg instanceof ServletRequest
                    || arg instanceof ServletResponse
                    || arg instanceof UserSession) {
                continue;
            }
            try {
                parts.add(objectMapper.writeValueAsString(arg));
            } catch (Exception ex) {
                parts.add(String.valueOf(arg));
            }
        }
        String json = String.join(", ", parts);
        return truncate(SENSITIVE_FIELD.matcher(json).replaceAll("$1\"***\""), MAX_PARAM_LENGTH);
    }

    private String truncate(String value, int maxLength) {
        if (value == null) {
            return null;
        }
        return value.length() <= maxLength ? value : value.substring(0, maxLength);
    }
}
