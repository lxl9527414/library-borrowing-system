package com.library.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.library.common.BusinessException;
import com.library.common.FineStatus;
import com.library.entity.Book;
import com.library.entity.BorrowRecord;
import com.library.entity.BorrowRule;
import com.library.entity.Role;
import com.library.mapper.BookMapper;
import com.library.mapper.BorrowRecordMapper;
import com.library.security.UserSession;
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
import java.time.Duration;
import java.time.LocalDateTime;
import java.util.List;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class BorrowServiceTest {

    @BeforeAll
    static void initTableInfo() {
        MybatisPlusTestSupport.initTableInfo(Book.class, BorrowRecord.class);
    }

    @Mock
    private BookMapper bookMapper;
    @Mock
    private BorrowRecordMapper borrowRecordMapper;
    @Mock
    private BorrowRuleService borrowRuleService;
    @Mock
    private ReservationService reservationService;

    @InjectMocks
    private BorrowService borrowService;

    private UserSession reader;
    private UserSession admin;
    private BorrowRule rule;

    @BeforeEach
    void setUp() {
        reader = new UserSession(2L, "reader", "普通读者", Role.READER.name());
        admin = new UserSession(1L, "admin", "系统管理员", Role.ADMIN.name());
        rule = new BorrowRule();
        rule.setId(BorrowRule.CONFIG_ID);
        rule.setMaxBorrowCount(5);
        rule.setBorrowDays(30);
        rule.setMaxRenewCount(1);
        rule.setRenewDays(15);
        rule.setFinePerDay(new BigDecimal("0.50"));
        rule.setFineMax(new BigDecimal("10.00"));
        rule.setReserveLimit(3);
        rule.setHoldDays(3);
    }

    @Test
    @DisplayName("借阅：达到同时在借上限时拒绝，且不写借阅记录")
    void borrow_whenReachBorrowLimit_thenReject() {
        when(bookMapper.selectById(1L)).thenReturn(book(3));
        when(borrowRuleService.current()).thenReturn(rule);
        when(borrowRecordMapper.selectCount(any())).thenReturn(5L);

        assertThatThrownBy(() -> borrowService.borrow(1L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("同时借阅上限为 5 本");

        verify(borrowRecordMapper, never()).insert(any(BorrowRecord.class));
    }

    @Test
    @DisplayName("借阅：无库存时提示可预约排队")
    void borrow_whenNoStock_thenReject() {
        when(bookMapper.selectById(1L)).thenReturn(book(0));
        when(borrowRuleService.current()).thenReturn(rule);
        when(borrowRecordMapper.selectCount(any())).thenReturn(0L);
        when(reservationService.findReady(1L, 2L)).thenReturn(null);
        when(reservationService.existsReadyForOther(1L, 2L)).thenReturn(false);

        assertThatThrownBy(() -> borrowService.borrow(1L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("暂无可借库存");
    }

    @Test
    @DisplayName("借阅：成功后按规则写入应还时间并完成预约")
    void borrow_success() {
        when(bookMapper.selectById(1L)).thenReturn(book(2));
        when(borrowRuleService.current()).thenReturn(rule);
        when(borrowRecordMapper.selectCount(any())).thenReturn(0L);
        when(reservationService.findReady(1L, 2L)).thenReturn(null);
        when(reservationService.existsReadyForOther(1L, 2L)).thenReturn(false);
        when(bookMapper.update(isNull(), any())).thenReturn(1);

        LocalDateTime before = LocalDateTime.now();
        BorrowRecord saved = borrowService.borrow(1L, reader);

        assertThat(saved.getBorrowNo()).startsWith("BR");
        assertThat(saved.getStatus()).isEqualTo("BORROWED");
        assertThat(saved.getRenewCount()).isZero();
        assertThat(saved.getFineStatus()).isEqualTo(FineStatus.NONE);
        assertThat(Duration.between(before, saved.getDueAt()).toDays()).isEqualTo(30);
        verify(borrowRecordMapper).insert(any(BorrowRecord.class));
        verify(reservationService).completeOnBorrow(1L, 2L);
    }

    @Test
    @DisplayName("续借：已逾期不允许续借")
    void renew_whenOverdue_thenReject() {
        when(borrowRecordMapper.selectById(9L))
                .thenReturn(record("BORROWED", LocalDateTime.now().minusDays(1), 0));
        when(borrowRuleService.current()).thenReturn(rule);

        assertThatThrownBy(() -> borrowService.renew(9L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("已逾期");
    }

    @Test
    @DisplayName("续借：达到最大续借次数时拒绝")
    void renew_whenReachMaxRenewCount_thenReject() {
        when(borrowRecordMapper.selectById(9L))
                .thenReturn(record("BORROWED", LocalDateTime.now().plusDays(5), 1));
        when(borrowRuleService.current()).thenReturn(rule);

        assertThatThrownBy(() -> borrowService.renew(9L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("最多可续借 1 次");
    }

    @Test
    @DisplayName("续借：成功时借期顺延并累加续借次数")
    void renew_success() {
        BorrowRecord existing = record("BORROWED", LocalDateTime.now().plusDays(5), 0);
        LocalDateTime originalDueAt = existing.getDueAt();
        when(borrowRecordMapper.selectById(9L)).thenReturn(existing);
        when(borrowRuleService.current()).thenReturn(rule);
        when(reservationService.existsActiveForOther(1L, 2L)).thenReturn(false);

        BorrowRecord renewed = borrowService.renew(9L, reader);

        assertThat(renewed.getRenewCount()).isEqualTo(1);
        assertThat(Duration.between(originalDueAt, renewed.getDueAt()).toDays()).isEqualTo(15);
        verify(borrowRecordMapper).updateById(existing);
    }

    @Test
    @DisplayName("归还：未逾期不产生罚金，并触发预约顺延")
    void returnBook_whenNotOverdue_thenNoFine() {
        when(borrowRecordMapper.selectById(9L))
                .thenReturn(record("BORROWED", LocalDateTime.now().plusDays(3), 0));

        BorrowRecord returned = borrowService.returnBook(9L, reader);

        assertThat(returned.getStatus()).isEqualTo("RETURNED");
        assertThat(returned.getFineAmount()).isEqualByComparingTo("0.00");
        assertThat(returned.getFineStatus()).isEqualTo(FineStatus.NONE);
        verify(bookMapper).update(isNull(), any());
        verify(reservationService).promoteNext(1L);
    }

    @Test
    @DisplayName("归还：逾期 5 天按每日 0.5 元计算罚金")
    void returnBook_whenOverdue_thenFineCalculated() {
        when(borrowRecordMapper.selectById(9L))
                .thenReturn(record("BORROWED", LocalDateTime.now().minusDays(5).plusMinutes(5), 0));
        when(borrowRuleService.current()).thenReturn(rule);

        BorrowRecord returned = borrowService.returnBook(9L, reader);

        assertThat(returned.getFineAmount()).isEqualByComparingTo("2.50");
        assertThat(returned.getFineStatus()).isEqualTo(FineStatus.UNPAID);
    }

    @Test
    @DisplayName("归还：逾期罚金不超过规则上限")
    void returnBook_whenLongOverdue_thenFineCapped() {
        when(borrowRecordMapper.selectById(9L))
                .thenReturn(record("BORROWED", LocalDateTime.now().minusDays(100), 0));
        when(borrowRuleService.current()).thenReturn(rule);

        BorrowRecord returned = borrowService.returnBook(9L, reader);

        assertThat(returned.getFineAmount()).isEqualByComparingTo("10.00");
    }

    @Test
    @DisplayName("归还：读者只能归还自己的记录")
    void returnBook_byOtherReader_thenForbidden() {
        BorrowRecord other = record("BORROWED", LocalDateTime.now().plusDays(3), 0);
        other.setUserId(3L);
        when(borrowRecordMapper.selectById(9L)).thenReturn(other);

        assertThatThrownBy(() -> borrowService.returnBook(9L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("只能归还自己的借阅记录");
    }

    @Test
    @DisplayName("归还：管理员可以代读者归还")
    void returnBook_byAdmin_thenAllowed() {
        BorrowRecord other = record("BORROWED", LocalDateTime.now().plusDays(3), 0);
        other.setUserId(3L);
        when(borrowRecordMapper.selectById(9L)).thenReturn(other);

        BorrowRecord returned = borrowService.returnBook(9L, admin);

        assertThat(returned.getStatus()).isEqualTo("RETURNED");
    }

    @Test
    @DisplayName("罚金缴纳：无待缴罚金时拒绝")
    void settleFine_whenNothingUnpaid_thenReject() {
        BorrowRecord settled = record("RETURNED", LocalDateTime.now().minusDays(2), 0);
        settled.setFineStatus(FineStatus.NONE);
        when(borrowRecordMapper.selectById(9L)).thenReturn(settled);

        assertThatThrownBy(() -> borrowService.settleFine(9L))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("没有待缴纳的罚金");
    }

    @Test
    @DisplayName("罚金缴纳：状态流转为已缴纳")
    void settleFine_success() {
        BorrowRecord unpaid = record("RETURNED", LocalDateTime.now().minusDays(2), 0);
        unpaid.setFineStatus(FineStatus.UNPAID);
        unpaid.setFineAmount(new BigDecimal("2.50"));
        when(borrowRecordMapper.selectById(9L)).thenReturn(unpaid);

        BorrowRecord result = borrowService.settleFine(9L);

        assertThat(result.getFineStatus()).isEqualTo(FineStatus.PAID);
        verify(borrowRecordMapper).updateById(unpaid);
    }

    @Test
    @DisplayName("分页：读者只查自己的记录，逾期标记正确")
    void page_marksOverdueRecords() {
        BorrowRecordVO overdue = new BorrowRecordVO();
        overdue.setStatus("BORROWED");
        overdue.setDueAt(LocalDateTime.now().minusDays(1));
        Page<BorrowRecordVO> page = new Page<>(1, 10);
        page.setRecords(List.of(overdue));
        page.setTotal(1);
        when(borrowRecordMapper.selectRecordPage(any(), eq(2L), isNull(), isNull())).thenReturn(page);

        IPage<BorrowRecordVO> result = borrowService.page(1, 10, null, null, reader);

        ArgumentCaptor<Long> userIdCaptor = ArgumentCaptor.forClass(Long.class);
        verify(borrowRecordMapper).selectRecordPage(any(), userIdCaptor.capture(), isNull(), isNull());
        assertThat(userIdCaptor.getValue()).isEqualTo(2L);
        assertThat(result.getRecords()).hasSize(1);
        assertThat(result.getRecords().get(0).getOverdue()).isTrue();
    }

    private Book book(int availableStock) {
        Book book = new Book();
        book.setId(1L);
        book.setTitle("Java编程思想");
        book.setIsbn("9787111213826");
        book.setTotalStock(5);
        book.setAvailableStock(availableStock);
        return book;
    }

    private BorrowRecord record(String status, LocalDateTime dueAt, int renewCount) {
        BorrowRecord record = new BorrowRecord();
        record.setId(9L);
        record.setUserId(2L);
        record.setBookId(1L);
        record.setBookTitle("Java编程思想");
        record.setBookIsbn("9787111213826");
        record.setStatus(status);
        record.setDueAt(dueAt);
        record.setRenewCount(renewCount);
        record.setFineAmount(BigDecimal.ZERO);
        record.setFineStatus(FineStatus.NONE);
        return record;
    }
}
