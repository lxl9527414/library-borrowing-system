package com.library.ai.controller;

import com.library.ai.dto.AiBorrowRequest;
import com.library.ai.dto.AiReserveRequest;
import com.library.ai.dto.AiReturnRequest;
import com.library.ai.service.AiToolService;
import com.library.ai.vo.AiBookDetailVO;
import com.library.ai.vo.AiBookVO;
import com.library.ai.vo.AiBorrowRecordVO;
import com.library.ai.vo.AiContextVO;
import com.library.ai.vo.AiReservationVO;
import com.library.ai.vo.AiRuleVO;
import com.library.ai.vo.AiRulesTextVO;
import com.library.common.GlobalExceptionHandler;
import com.library.common.PageResult;
import com.library.common.ReservationStatus;
import com.library.entity.BookReservation;
import com.library.entity.BorrowRecord;
import com.library.log.Auditable;
import com.library.security.AuthContext;
import com.library.security.UserSession;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.request.MockHttpServletRequestBuilder;
import org.springframework.test.web.servlet.setup.MockMvcBuilders;

import java.lang.reflect.Field;
import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.Arrays;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

@ExtendWith(MockitoExtension.class)
class AiToolControllerTest {

    @Mock
    private AiToolService aiToolService;

    private MockMvc mockMvc;
    private UserSession reader;

    @BeforeEach
    void setUp() {
        mockMvc = MockMvcBuilders
                .standaloneSetup(new AiToolController(aiToolService))
                .setControllerAdvice(new GlobalExceptionHandler())
                .build();
        reader = new UserSession(2L, "reader", "张小明", "READER");
    }

    @Test
    @DisplayName("上下文接口：返回统一 ApiResponse，包含用户、规则与统计")
    void context_returnsContext() throws Exception {
        AiContextVO context = new AiContextVO(reader, rule(), 2L, 1L, 1L, 0L);
        when(aiToolService.context(reader)).thenReturn(context);

        mockMvc.perform(withReader(get("/api/ai/internal/context")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.message").value("操作成功"))
                .andExpect(jsonPath("$.data.user.id").value(2))
                .andExpect(jsonPath("$.data.user.username").value("reader"))
                .andExpect(jsonPath("$.data.user.realName").value("张小明"))
                .andExpect(jsonPath("$.data.user.role").value("READER"))
                .andExpect(jsonPath("$.data.rule.maxBorrowCount").value(3))
                .andExpect(jsonPath("$.data.rule.borrowDays").value(30))
                .andExpect(jsonPath("$.data.borrowedCount").value(2))
                .andExpect(jsonPath("$.data.overdueCount").value(1))
                .andExpect(jsonPath("$.data.activeReservationCount").value(1))
                .andExpect(jsonPath("$.data.pendingPickupCount").value(0));

        ArgumentCaptor<UserSession> userCaptor = ArgumentCaptor.forClass(UserSession.class);
        verify(aiToolService).context(userCaptor.capture());
        assertThat(userCaptor.getValue()).isSameAs(reader);
    }

    @Test
    @DisplayName("规则文案接口：返回自然语言规则文本")
    void rulesText_returnsText() throws Exception {
        when(aiToolService.rulesText()).thenReturn(new AiRulesTextVO("每人最多同时借阅 3 本，借期 30 天"));

        mockMvc.perform(get("/api/ai/internal/rules/text"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.text").value("每人最多同时借阅 3 本，借期 30 天"));
    }

    @Test
    @DisplayName("检索图书接口：透传查询参数并返回分页结构")
    void books_returnsPage() throws Exception {
        when(aiToolService.searchBooks("三体", "科幻", 1L, 5L))
                .thenReturn(new PageResult<>(List.of(book()), 1L, 1L, 5L));

        mockMvc.perform(get("/api/ai/internal/books")
                        .param("keyword", "三体")
                        .param("category", "科幻"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.records[0].id").value(1))
                .andExpect(jsonPath("$.data.records[0].isbn").value("9787536692930"))
                .andExpect(jsonPath("$.data.records[0].title").value("三体"))
                .andExpect(jsonPath("$.data.records[0].author").value("刘慈欣"))
                .andExpect(jsonPath("$.data.records[0].availableStock").value(0))
                .andExpect(jsonPath("$.data.total").value(1))
                .andExpect(jsonPath("$.data.current").value(1))
                .andExpect(jsonPath("$.data.size").value(5));
    }

    @Test
    @DisplayName("图书详情接口：返回库存、预约队列长度与我的预约")
    void bookDetail_returnsDetail() throws Exception {
        AiReservationVO myReservation = new AiReservationVO(
                9L, 1L, ReservationStatus.READY, LocalDateTime.now(), LocalDateTime.now().plusDays(3));
        when(aiToolService.bookDetail(1L, reader))
                .thenReturn(new AiBookDetailVO(book(), 2L, myReservation));

        mockMvc.perform(withReader(get("/api/ai/internal/books/1")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.book.id").value(1))
                .andExpect(jsonPath("$.data.book.title").value("三体"))
                .andExpect(jsonPath("$.data.waitingCount").value(2))
                .andExpect(jsonPath("$.data.myReservation.id").value(9))
                .andExpect(jsonPath("$.data.myReservation.status").value("READY"));
    }

    @Test
    @DisplayName("我的在借接口：返回逾期与罚金字段")
    void myBorrows_returnsRecords() throws Exception {
        AiBorrowRecordVO record = new AiBorrowRecordVO(
                10L, 1L, "三体", "9787536692930", "BORROWED",
                LocalDateTime.now().minusDays(20), LocalDateTime.now().minusDays(1),
                true, 1L, 0, new BigDecimal("0.20"), "UNPAID");
        when(aiToolService.myBorrows(reader)).thenReturn(List.of(record));

        mockMvc.perform(withReader(get("/api/ai/internal/borrows/my")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data[0].id").value(10))
                .andExpect(jsonPath("$.data[0].status").value("BORROWED"))
                .andExpect(jsonPath("$.data[0].overdue").value(true))
                .andExpect(jsonPath("$.data[0].overdueDays").value(1))
                .andExpect(jsonPath("$.data[0].fineStatus").value("UNPAID"));
    }

    @Test
    @DisplayName("借书接口：当前登录用户透传给服务层，返回 BorrowRecord")
    void borrow_returnsBorrowRecord() throws Exception {
        when(aiToolService.borrow(1L, reader)).thenReturn(borrowRecord("BORROWED"));

        mockMvc.perform(withReader(post("/api/ai/internal/borrow"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"bookId\":1}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.id").value(11))
                .andExpect(jsonPath("$.data.bookId").value(1))
                .andExpect(jsonPath("$.data.status").value("BORROWED"));

        ArgumentCaptor<UserSession> userCaptor = ArgumentCaptor.forClass(UserSession.class);
        verify(aiToolService).borrow(eq(1L), userCaptor.capture());
        assertThat(userCaptor.getValue()).isSameAs(reader);
    }

    @Test
    @DisplayName("借书接口：请求体伪造的 userId 被忽略，身份只来自 token 解析结果")
    void borrow_ignoresUserIdInBody() throws Exception {
        when(aiToolService.borrow(1L, reader)).thenReturn(borrowRecord("BORROWED"));

        mockMvc.perform(withReader(post("/api/ai/internal/borrow"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"bookId\":1,\"userId\":999}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200));

        ArgumentCaptor<UserSession> userCaptor = ArgumentCaptor.forClass(UserSession.class);
        verify(aiToolService).borrow(eq(1L), userCaptor.capture());
        assertThat(userCaptor.getValue().getId()).isEqualTo(2L);
        assertThat(userCaptor.getValue().getUsername()).isEqualTo("reader");
    }

    @Test
    @DisplayName("还书接口：当前登录用户透传给服务层，返回 BorrowRecord")
    void returnBook_returnsBorrowRecord() throws Exception {
        when(aiToolService.returnBook(10L, reader)).thenReturn(borrowRecord("RETURNED"));

        mockMvc.perform(withReader(post("/api/ai/internal/return"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"borrowId\":10}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.data.status").value("RETURNED"));

        ArgumentCaptor<UserSession> userCaptor = ArgumentCaptor.forClass(UserSession.class);
        verify(aiToolService).returnBook(eq(10L), userCaptor.capture());
        assertThat(userCaptor.getValue()).isSameAs(reader);
    }

    @Test
    @DisplayName("预约接口：当前登录用户透传给服务层，返回 BookReservation")
    void reserve_returnsReservation() throws Exception {
        BookReservation saved = new BookReservation();
        saved.setId(9L);
        saved.setBookId(1L);
        saved.setUserId(2L);
        saved.setStatus(ReservationStatus.WAITING);
        when(aiToolService.reserve(1L, reader)).thenReturn(saved);

        mockMvc.perform(withReader(post("/api/ai/internal/reserve"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{\"bookId\":1}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(200))
                .andExpect(jsonPath("$.data.id").value(9))
                .andExpect(jsonPath("$.data.status").value("WAITING"));

        ArgumentCaptor<UserSession> userCaptor = ArgumentCaptor.forClass(UserSession.class);
        verify(aiToolService).reserve(eq(1L), userCaptor.capture());
        assertThat(userCaptor.getValue()).isSameAs(reader);
    }

    @Test
    @DisplayName("写接口：缺少必填参数时由全局异常处理器返回 code=400 与中文提示")
    void borrow_whenBookIdMissing_thenBadRequest() throws Exception {
        mockMvc.perform(withReader(post("/api/ai/internal/borrow"))
                        .contentType(MediaType.APPLICATION_JSON)
                        .content("{}"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.code").value(400))
                .andExpect(jsonPath("$.message").value("请选择要借阅的图书"));

        verify(aiToolService, never()).borrow(any(), any());
    }

    @Test
    @DisplayName("写接口：三个写操作都标注 @Auditable，复用现有操作日志切面")
    void writeEndpoints_areAuditable() {
        Auditable borrow = method("borrow").getAnnotation(Auditable.class);
        assertThat(borrow).isNotNull();
        assertThat(borrow.module()).isEqualTo("AI工具");
        assertThat(borrow.action()).isEqualTo("AI借阅图书");

        Auditable returnBook = method("returnBook").getAnnotation(Auditable.class);
        assertThat(returnBook).isNotNull();
        assertThat(returnBook.module()).isEqualTo("AI工具");
        assertThat(returnBook.action()).isEqualTo("AI归还图书");

        Auditable reserve = method("reserve").getAnnotation(Auditable.class);
        assertThat(reserve).isNotNull();
        assertThat(reserve.module()).isEqualTo("AI工具");
        assertThat(reserve.action()).isEqualTo("AI预约图书");
    }

    @Test
    @DisplayName("请求体 DTO 不包含 userId 字段，LLM 无法伪造借阅/预约人")
    void requestDtos_haveNoUserIdField() {
        assertThat(fieldNames(AiBorrowRequest.class)).doesNotContain("userId").containsExactly("bookId");
        assertThat(fieldNames(AiReturnRequest.class)).doesNotContain("userId").containsExactly("borrowId");
        assertThat(fieldNames(AiReserveRequest.class)).doesNotContain("userId").containsExactly("bookId");
    }

    private MockHttpServletRequestBuilder withReader(MockHttpServletRequestBuilder builder) {
        return builder.requestAttr(AuthContext.CURRENT_USER_ATTRIBUTE, reader);
    }

    private Method method(String name) {
        return Arrays.stream(AiToolController.class.getDeclaredMethods())
                .filter(candidate -> candidate.getName().equals(name))
                .findFirst()
                .orElseThrow();
    }

    private List<String> fieldNames(Class<?> type) {
        return Arrays.stream(type.getDeclaredFields()).map(Field::getName).toList();
    }

    private AiRuleVO rule() {
        return new AiRuleVO(3, 30, 1, 15,
                new BigDecimal("0.20"), new BigDecimal("20.00"), 3, 3);
    }

    private AiBookVO book() {
        return new AiBookVO(1L, "9787536692930", "三体", "刘慈欣", "重庆出版社", "科幻",
                new BigDecimal("23.00"), 2, 0, "地球往事");
    }

    private BorrowRecord borrowRecord(String recordStatus) {
        BorrowRecord record = new BorrowRecord();
        record.setId(11L);
        record.setBookId(1L);
        record.setUserId(2L);
        record.setStatus(recordStatus);
        record.setDueAt(LocalDateTime.now().plusDays(30));
        return record;
    }
}
