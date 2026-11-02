package com.library.service;

import com.library.common.BusinessException;
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
import com.library.support.MybatisPlusTestSupport;
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
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class ReservationServiceTest {

    @BeforeAll
    static void initTableInfo() {
        MybatisPlusTestSupport.initTableInfo(BookReservation.class, BorrowRecord.class, Book.class);
    }

    @Mock
    private BookReservationMapper reservationMapper;
    @Mock
    private BookMapper bookMapper;
    @Mock
    private BorrowRecordMapper borrowRecordMapper;
    @Mock
    private BorrowRuleService borrowRuleService;

    @InjectMocks
    private ReservationService reservationService;

    private UserSession reader;
    private BorrowRule rule;

    @BeforeEach
    void setUp() {
        reader = new UserSession(2L, "reader", "普通读者", Role.READER.name());
        rule = new BorrowRule();
        rule.setReserveLimit(2);
        rule.setHoldDays(3);
        rule.setFinePerDay(new BigDecimal("0.50"));
        rule.setFineMax(new BigDecimal("10.00"));
    }

    @Test
    @DisplayName("预约：有可借库存时提示直接借阅")
    void reserve_whenStockAvailable_thenReject() {
        when(bookMapper.selectById(1L)).thenReturn(book(2));

        assertThatThrownBy(() -> reservationService.reserve(1L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("可直接借阅");
    }

    @Test
    @DisplayName("预约：已借阅该书时不允许预约")
    void reserve_whenAlreadyBorrowed_thenReject() {
        when(bookMapper.selectById(1L)).thenReturn(book(0));
        when(borrowRecordMapper.selectCount(any())).thenReturn(1L);

        assertThatThrownBy(() -> reservationService.reserve(1L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("已借阅该图书且尚未归还");
    }

    @Test
    @DisplayName("预约：同一本书不允许重复预约")
    void reserve_whenDuplicate_thenReject() {
        when(bookMapper.selectById(1L)).thenReturn(book(0));
        when(borrowRecordMapper.selectCount(any())).thenReturn(0L);
        when(reservationMapper.selectCount(any())).thenReturn(1L);

        assertThatThrownBy(() -> reservationService.reserve(1L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("请勿重复预约");
    }

    @Test
    @DisplayName("预约：超过预约数量上限时拒绝")
    void reserve_whenReachReserveLimit_thenReject() {
        when(bookMapper.selectById(1L)).thenReturn(book(0));
        when(borrowRecordMapper.selectCount(any())).thenReturn(0L);
        when(reservationMapper.selectCount(any())).thenReturn(0L, 2L);
        when(borrowRuleService.current()).thenReturn(rule);

        assertThatThrownBy(() -> reservationService.reserve(1L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("最多可同时预约 2 本图书");
    }

    @Test
    @DisplayName("预约：成功写入排队中记录")
    void reserve_success() {
        when(bookMapper.selectById(1L)).thenReturn(book(0));
        when(borrowRecordMapper.selectCount(any())).thenReturn(0L);
        when(reservationMapper.selectCount(any())).thenReturn(0L, 0L);
        when(borrowRuleService.current()).thenReturn(rule);

        BookReservation created = reservationService.reserve(1L, reader);

        assertThat(created.getStatus()).isEqualTo(ReservationStatus.WAITING);
        assertThat(created.getUserId()).isEqualTo(2L);
        assertThat(created.getBookId()).isEqualTo(1L);
        verify(reservationMapper).insert(created);
    }

    @Test
    @DisplayName("取消预约：不能取消别人的预约")
    void cancel_byOtherReader_thenForbidden() {
        when(reservationMapper.selectById(5L)).thenReturn(reservation(5L, 3L, ReservationStatus.WAITING));

        assertThatThrownBy(() -> reservationService.cancel(5L, reader))
                .isInstanceOf(BusinessException.class)
                .hasMessageContaining("只能取消自己的预约");
    }

    @Test
    @DisplayName("取消预约：释放待取书名额并顺延给下一位")
    void cancel_whenReady_thenPromoteNext() {
        when(reservationMapper.selectById(5L)).thenReturn(reservation(5L, 2L, ReservationStatus.READY));
        when(reservationMapper.selectCount(any())).thenReturn(0L);
        when(reservationMapper.selectOne(any())).thenReturn(reservation(6L, 3L, ReservationStatus.WAITING));
        when(borrowRuleService.current()).thenReturn(rule);

        reservationService.cancel(5L, reader);

        ArgumentCaptor<BookReservation> captor = ArgumentCaptor.forClass(BookReservation.class);
        verify(reservationMapper, times(2)).updateById(captor.capture());
        List<BookReservation> updated = captor.getAllValues();
        assertThat(updated.get(0).getStatus()).isEqualTo(ReservationStatus.CANCELED);
        assertThat(updated.get(1).getStatus()).isEqualTo(ReservationStatus.READY);
        assertThat(updated.get(1).getExpireAt()).isNotNull();
    }

    @Test
    @DisplayName("顺延：已有待取书记录时不再提升新队列")
    void promoteNext_whenReadyExists_thenSkip() {
        when(reservationMapper.selectCount(any())).thenReturn(1L);

        reservationService.promoteNext(1L);

        verify(reservationMapper, never()).selectOne(any());
        verify(reservationMapper, never()).updateById(any(BookReservation.class));
    }

    @Test
    @DisplayName("顺延：队首按保留天数转为待取书")
    void promoteNext_setsReadyWithExpireAt() {
        when(reservationMapper.selectCount(any())).thenReturn(0L);
        BookReservation waiting = reservation(7L, 4L, ReservationStatus.WAITING);
        when(reservationMapper.selectOne(any())).thenReturn(waiting);
        when(borrowRuleService.current()).thenReturn(rule);

        LocalDateTime before = LocalDateTime.now();
        reservationService.promoteNext(1L);

        assertThat(waiting.getStatus()).isEqualTo(ReservationStatus.READY);
        assertThat(waiting.getReadyAt()).isNotNull();
        assertThat(Duration.between(before, waiting.getExpireAt()).toDays()).isEqualTo(3);
        verify(reservationMapper).updateById(waiting);
    }

    @Test
    @DisplayName("超时释放：过期待取书置为失效并顺延")
    void expireReadyReservations_releasesExpired() {
        BookReservation expired = reservation(8L, 2L, ReservationStatus.READY);
        expired.setExpireAt(LocalDateTime.now().minusHours(1));
        when(reservationMapper.selectList(any())).thenReturn(List.of(expired));
        when(reservationMapper.selectCount(any())).thenReturn(0L);
        when(reservationMapper.selectOne(any())).thenReturn(null);

        int released = reservationService.expireReadyReservations();

        assertThat(released).isEqualTo(1);
        assertThat(expired.getStatus()).isEqualTo(ReservationStatus.EXPIRED);
        verify(reservationMapper).updateById(expired);
    }

    private Book book(int availableStock) {
        Book book = new Book();
        book.setId(1L);
        book.setTitle("百年孤独");
        book.setIsbn("9787544270878");
        book.setTotalStock(3);
        book.setAvailableStock(availableStock);
        return book;
    }

    private BookReservation reservation(Long id, Long userId, String status) {
        BookReservation reservation = new BookReservation();
        reservation.setId(id);
        reservation.setUserId(userId);
        reservation.setBookId(1L);
        reservation.setStatus(status);
        return reservation;
    }
}
