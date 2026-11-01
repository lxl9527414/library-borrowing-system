package com.library.ai.service;

import com.baomidou.mybatisplus.core.conditions.AbstractWrapper;
import com.baomidou.mybatisplus.core.conditions.Wrapper;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.library.ai.vo.AiBookDetailVO;
import com.library.ai.vo.AiBookVO;
import com.library.ai.vo.AiBorrowRecordVO;
import com.library.ai.vo.AiContextVO;
import com.library.common.BusinessException;
import com.library.common.FineStatus;
import com.library.common.PageResult;
import com.library.common.ReservationStatus;
import com.library.entity.Book;
import com.library.entity.BookReservation;
import com.library.entity.BorrowRecord;
import com.library.entity.BorrowRule;
import com.library.entity.Role;
import com.library.mapper.BookMapper;
import com.library.mapper.BookReservationMapper;
import com.library.mapper.BorrowRecordMapper;
import com.library.security.UserSession;
import com.library.service.BookService;
import com.library.service.BorrowRuleService;
import com.library.service.BorrowService;
import com.library.service.ReservationService;
import com.library.support.MybatisPlusTestSupport;
import com.library.vo.BorrowRecordVO;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyLong;
import static org.mockito.ArgumentMatchers.argThat;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class AiToolServiceTest {

    @BeforeAll
    static void initTableInfo() {
        MybatisPlusTestSupport.initTableInfo(Book.class, BorrowRecord.class, BookReservation.class);
    }

    @Mock
    private BookService bookService;
    @Mock
    private BookMapper bookMapper;
    @Mock
    private BorrowService borrowService;
    @Mock
    private BorrowRecordMapper borrowRecordMapper;
    @Mock
    private ReservationService reservationService;
    @Mock
    private BorrowRuleService borrowRuleService;
    @Mock
    private BookReservationMapper bookReservationMapper;

    @InjectMocks
    private AiToolService aiToolService;

    private UserSession reader;
    private BorrowRule rule;

    @BeforeEach
    void setUp() {
        reader = new UserSession(2L, "reader", "张小明", Role.READER.name());
        rule = new BorrowRule();
        rule.setId(BorrowRule.CONFIG_ID);
        rule.setMaxBorrowCount(3);
        rule.setBorrowDays(30);
        rule.setMaxRenewCount(1);
        rule.setRenewDays(15);
        rule.setFinePerDay(new BigDecimal("0.20"));
        rule.setFineMax(new BigDecimal("20.00"));
        rule.setReserveLimit(3);
        rule.setHoldDays(3);
    }

    @Test
    @DisplayName("上下文：返回当前用户、借阅规则与四项个人统计")
    void context_returnsUserRuleAndCounts() {
        when(borrowRuleService.current()).thenReturn(rule);
        when(borrowRecordMapper.selectCount(any())).thenReturn(2L, 1L);
        when(reservationService.activeList(2L)).thenReturn(List.of(
                reservation(9L, 1L, ReservationStatus.READY),
                reservation(10L, 2L, ReservationStatus.WAITING)));

        AiContextVO context = aiToolService.context(reader);

        assertThat(context.getUser()).isSameAs(reader);
        assertThat(context.getRule().getMaxBorrowCount()).isEqualTo(3);
        assertThat(context.getRule().getBorrowDays()).isEqualTo(30);
        assertThat(context.getRule().getFinePerDay()).isEqualByComparingTo("0.20");
        assertThat(context.getRule().getFineMax()).isEqualByComparingTo("20.00");
        assertThat(context.getBorrowedCount()).isEqualTo(2L);
        assertThat(context.getOverdueCount()).isEqualTo(1L);
        assertThat(context.getActiveReservationCount()).isEqualTo(2L);
        assertThat(context.getPendingPickupCount()).isEqualTo(1L);
    }

    @Test
    @DisplayName("上下文：借阅/逾期统计与预约列表只按当前登录用户过滤，读不到他人数据")
    void context_filtersByCurrentUserOnly() {
        when(borrowRuleService.current()).thenReturn(rule);
        when(borrowRecordMapper.selectCount(any())).thenReturn(2L, 1L);
        when(reservationService.activeList(2L)).thenReturn(List.of());

        aiToolService.context(reader);

        verify(borrowRecordMapper, times(2)).selectCount(argThat(wrapper ->
                paramValues(wrapper).contains(2L)));
        verify(reservationService).activeList(2L);
    }

    @Test
    @DisplayName("规则文案：把借阅规则转换成中文自然语言，供 prompt 注入")
    void rulesText_containsRuleValues() {
        when(borrowRuleService.current()).thenReturn(rule);

        String text = aiToolService.rulesText().getText();

        assertThat(text)
                .contains("每人最多同时借阅 3 本")
                .contains("借期 30 天")
                .contains("最多续借 1 次")
                .contains("每次 15 天")
                .contains("逾期每天罚金 0.20 元")
                .contains("单笔封顶 20.00 元")
                .contains("最多可同时预约 3 本图书")
                .contains("待取书保留 3 天");
    }

    @Test
    @DisplayName("检索图书：按契约字段映射，并透传分页信息")
    void searchBooks_mapsBookFields() {
        Page<Book> page = new Page<>(1, 5);
        page.setRecords(List.of(book(1L, "三体", 2, 0)));
        page.setTotal(1);
        when(bookService.page(1L, 5L, "三体", "科幻")).thenReturn(page);

        PageResult<AiBookVO> result = aiToolService.searchBooks("三体", "科幻", 1, 5);

        assertThat(result.getTotal()).isEqualTo(1L);
        assertThat(result.getCurrent()).isEqualTo(1L);
        assertThat(result.getSize()).isEqualTo(5L);
        assertThat(result.getRecords()).hasSize(1);
        AiBookVO vo = result.getRecords().get(0);
        assertThat(vo.getId()).isEqualTo(1L);
        assertThat(vo.getIsbn()).isEqualTo("9787536692930");
        assertThat(vo.getTitle()).isEqualTo("三体");
        assertThat(vo.getAuthor()).isEqualTo("刘慈欣");
        assertThat(vo.getTotalStock()).isEqualTo(2);
        assertThat(vo.getAvailableStock()).isZero();
        assertThat(vo.getDescription()).isEqualTo("地球往事");
    }

    @Test
    @DisplayName("检索图书：分页大小收敛到契约要求的 1~20，非法值回落默认 5")
    void searchBooks_clampsPageSize() {
        when(bookService.page(anyLong(), anyLong(), isNull(), isNull())).thenReturn(new Page<>(1, 20));

        aiToolService.searchBooks(null, null, 0, 100);
        aiToolService.searchBooks(null, null, 1, 0);

        verify(bookService).page(1L, 20L, null, null);
        verify(bookService).page(1L, 5L, null, null);
    }

    @Test
    @DisplayName("图书详情：返回库存、预约队列长度与我的预约")
    void bookDetail_returnsQueueAndMyReservation() {
        when(bookMapper.selectById(1L)).thenReturn(book(1L, "三体", 2, 0));
        when(bookReservationMapper.selectCount(any())).thenReturn(2L);
        when(reservationService.activeList(2L)).thenReturn(List.of(
                reservation(8L, 1L, ReservationStatus.READY),
                reservation(9L, 2L, ReservationStatus.WAITING)));

        AiBookDetailVO detail = aiToolService.bookDetail(1L, reader);

        assertThat(detail.getBook().getTitle()).isEqualTo("三体");
        assertThat(detail.getBook().getAvailableStock()).isZero();
        assertThat(detail.getWaitingCount()).isEqualTo(2L);
        assertThat(detail.getMyReservation()).isNotNull();
        assertThat(detail.getMyReservation().getId()).isEqualTo(8L);
        assertThat(detail.getMyReservation().getStatus()).isEqualTo(ReservationStatus.READY);
        verify(reservationService).activeList(2L);
    }

    @Test
    @DisplayName("图书详情：当前用户没有预约时 myReservation 为 null")
    void bookDetail_whenNoReservation_thenMyReservationNull() {
        when(bookMapper.selectById(1L)).thenReturn(book(1L, "三体", 2, 2));
        when(bookReservationMapper.selectCount(any())).thenReturn(0L);
        when(reservationService.activeList(2L)).thenReturn(List.of(reservation(9L, 2L, ReservationStatus.WAITING)));

        AiBookDetailVO detail = aiToolService.bookDetail(1L, reader);

        assertThat(detail.getWaitingCount()).isZero();
        assertThat(detail.getMyReservation()).isNull();
    }

    @Test
    @DisplayName("图书详情：图书不存在时抛业务异常并提示中文原因")
    void bookDetail_whenBookMissing_thenReject() {
        when(bookMapper.selectById(1L)).thenReturn(null);

        assertThatThrownBy(() -> aiToolService.bookDetail(1L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("图书不存在");
    }

    @Test
    @DisplayName("我的在借：复用分页 VO 的逾期/罚金字段，并只查当前读者")
    void myBorrows_reusesBorrowRecordVO() {
        BorrowRecordVO overdue = new BorrowRecordVO();
        overdue.setId(10L);
        overdue.setBookId(1L);
        overdue.setBookTitle("三体");
        overdue.setBookIsbn("9787536692930");
        overdue.setStatus("BORROWED");
        overdue.setBorrowedAt(LocalDateTime.now().minusDays(20));
        overdue.setDueAt(LocalDateTime.now().minusDays(2).plusHours(12));
        overdue.setOverdue(true);
        overdue.setRenewCount(0);
        overdue.setFineAmount(new BigDecimal("0.40"));
        overdue.setFineStatus(FineStatus.UNPAID);

        BorrowRecordVO normal = new BorrowRecordVO();
        normal.setId(11L);
        normal.setBookId(2L);
        normal.setStatus("BORROWED");
        normal.setDueAt(LocalDateTime.now().plusDays(10));
        normal.setOverdue(false);

        Page<BorrowRecordVO> page = new Page<>(1, 20);
        page.setRecords(List.of(overdue, normal));
        page.setTotal(2);
        when(borrowService.page(1L, 20L, "BORROWED", null, reader)).thenReturn(page);

        List<AiBorrowRecordVO> records = aiToolService.myBorrows(reader);

        assertThat(records).hasSize(2);
        AiBorrowRecordVO overdueVO = records.get(0);
        assertThat(overdueVO.getId()).isEqualTo(10L);
        assertThat(overdueVO.getStatus()).isEqualTo("BORROWED");
        assertThat(overdueVO.getOverdue()).isTrue();
        assertThat(overdueVO.getOverdueDays()).isEqualTo(2L);
        assertThat(overdueVO.getFineAmount()).isEqualByComparingTo("0.40");
        assertThat(overdueVO.getFineStatus()).isEqualTo(FineStatus.UNPAID);
        assertThat(records.get(1).getOverdue()).isFalse();
        assertThat(records.get(1).getOverdueDays()).isZero();

        ArgumentCaptor<UserSession> userCaptor = ArgumentCaptor.forClass(UserSession.class);
        verify(borrowService).page(eq(1L), eq(20L), eq("BORROWED"), isNull(), userCaptor.capture());
        assertThat(userCaptor.getValue().getId()).isEqualTo(2L);
    }

    @Test
    @DisplayName("借书：把当前登录用户透传给 BorrowService，身份不来自请求体")
    void borrow_passesCurrentUser() {
        BorrowRecord saved = new BorrowRecord();
        saved.setId(11L);
        saved.setBookId(1L);
        saved.setUserId(2L);
        saved.setStatus("BORROWED");
        when(borrowService.borrow(1L, reader)).thenReturn(saved);

        BorrowRecord result = aiToolService.borrow(1L, reader);

        assertThat(result).isSameAs(saved);
        ArgumentCaptor<UserSession> userCaptor = ArgumentCaptor.forClass(UserSession.class);
        verify(borrowService).borrow(eq(1L), userCaptor.capture());
        assertThat(userCaptor.getValue()).isSameAs(reader);
        assertThat(userCaptor.getValue().getId()).isEqualTo(2L);
    }

    @Test
    @DisplayName("还书：读者身份透传给 BorrowService，由现有逻辑拒绝归还他人记录")
    void returnBook_passesCurrentUser() {
        BorrowRecord returned = new BorrowRecord();
        returned.setId(10L);
        returned.setStatus("RETURNED");
        when(borrowService.returnBook(10L, reader)).thenReturn(returned);

        BorrowRecord result = aiToolService.returnBook(10L, reader);

        assertThat(result.getStatus()).isEqualTo("RETURNED");
        ArgumentCaptor<UserSession> userCaptor = ArgumentCaptor.forClass(UserSession.class);
        verify(borrowService).returnBook(eq(10L), userCaptor.capture());
        assertThat(userCaptor.getValue().getId()).isEqualTo(2L);
        assertThat(userCaptor.getValue().getRole()).isEqualTo(Role.READER.name());
    }

    @Test
    @DisplayName("预约：当前登录用户透传给 ReservationService")
    void reserve_passesCurrentUser() {
        BookReservation saved = reservation(9L, 1L, ReservationStatus.WAITING);
        saved.setUserId(2L);
        when(reservationService.reserve(1L, reader)).thenReturn(saved);

        BookReservation result = aiToolService.reserve(1L, reader);

        assertThat(result).isSameAs(saved);
        ArgumentCaptor<UserSession> userCaptor = ArgumentCaptor.forClass(UserSession.class);
        verify(reservationService).reserve(eq(1L), userCaptor.capture());
        assertThat(userCaptor.getValue()).isSameAs(reader);
    }

    private List<Object> paramValues(Wrapper<BorrowRecord> wrapper) {
        // LambdaQueryWrapper 的条件参数是惰性格式化的，先渲染 SQL 片段才能取到参数值
        wrapper.getSqlSegment();
        return new ArrayList<>(((AbstractWrapper<?, ?, ?>) wrapper).getParamNameValuePairs().values());
    }

    private Book book(Long id, String title, int totalStock, int availableStock) {
        Book book = new Book();
        book.setId(id);
        book.setIsbn("9787536692930");
        book.setTitle(title);
        book.setAuthor("刘慈欣");
        book.setPublisher("重庆出版社");
        book.setCategory("科幻");
        book.setPrice(new BigDecimal("23.00"));
        book.setTotalStock(totalStock);
        book.setAvailableStock(availableStock);
        book.setDescription("地球往事");
        return book;
    }

    private BookReservation reservation(Long id, Long bookId, String status) {
        BookReservation reservation = new BookReservation();
        reservation.setId(id);
        reservation.setBookId(bookId);
        reservation.setUserId(2L);
        reservation.setStatus(status);
        return reservation;
    }
}
