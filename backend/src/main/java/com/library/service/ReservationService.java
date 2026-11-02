package com.library.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
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
import com.library.vo.BookReservationVO;
import lombok.RequiredArgsConstructor;
import org.springframework.dao.DuplicateKeyException;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.LocalDateTime;
import java.util.List;

@Service
@RequiredArgsConstructor
public class ReservationService {

    private final BookReservationMapper reservationMapper;
    private final BookMapper bookMapper;
    private final BorrowRecordMapper borrowRecordMapper;
    private final BorrowRuleService borrowRuleService;

    public IPage<BookReservationVO> page(long current, long size, String status, String keyword, UserSession currentUser) {
        Long userId = Role.ADMIN.name().equals(currentUser.getRole()) ? null : currentUser.getId();
        return reservationMapper.selectReservationPage(new Page<>(current, size), userId, status, keyword);
    }

    public List<BookReservation> activeList(Long userId) {
        return reservationMapper.selectList(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getUserId, userId)
                .in(BookReservation::getStatus, ReservationStatus.ACTIVE)
                .orderByAsc(BookReservation::getCreatedAt)
                .orderByAsc(BookReservation::getId));
    }

    @Transactional
    public BookReservation reserve(Long bookId, UserSession currentUser) {
        Book book = bookMapper.selectById(bookId);
        if (book == null) {
            throw new BusinessException("图书不存在");
        }
        if (book.getAvailableStock() != null && book.getAvailableStock() > 0) {
            throw new BusinessException("该图书当前有可借库存，可直接借阅");
        }

        long borrowed = borrowRecordMapper.selectCount(Wrappers.<BorrowRecord>lambdaQuery()
                .eq(BorrowRecord::getUserId, currentUser.getId())
                .eq(BorrowRecord::getBookId, bookId)
                .eq(BorrowRecord::getStatus, "BORROWED"));
        if (borrowed > 0) {
            throw new BusinessException("您已借阅该图书且尚未归还");
        }

        long sameBook = reservationMapper.selectCount(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getUserId, currentUser.getId())
                .eq(BookReservation::getBookId, bookId)
                .in(BookReservation::getStatus, ReservationStatus.ACTIVE));
        if (sameBook > 0) {
            throw new BusinessException("您已预约过该图书，请勿重复预约");
        }

        BorrowRule rule = borrowRuleService.current();
        long activeCount = reservationMapper.selectCount(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getUserId, currentUser.getId())
                .in(BookReservation::getStatus, ReservationStatus.ACTIVE));
        if (activeCount >= rule.getReserveLimit()) {
            throw new BusinessException("最多可同时预约 " + rule.getReserveLimit() + " 本图书");
        }

        BookReservation reservation = new BookReservation();
        reservation.setBookId(bookId);
        reservation.setUserId(currentUser.getId());
        reservation.setStatus(ReservationStatus.WAITING);
        try {
            reservationMapper.insert(reservation);
        } catch (DuplicateKeyException ex) {
            throw new BusinessException("您已预约过该图书，请勿重复预约");
        }
        return reservation;
    }

    @Transactional
    public void cancel(Long id, UserSession currentUser) {
        BookReservation reservation = requireReservation(id);
        if (!Role.ADMIN.name().equals(currentUser.getRole())
                && !currentUser.getId().equals(reservation.getUserId())) {
            throw new BusinessException(403, "只能取消自己的预约");
        }
        if (!ReservationStatus.ACTIVE.contains(reservation.getStatus())) {
            throw new BusinessException("该预约已结束，无法取消");
        }
        boolean holdReleased = ReservationStatus.READY.equals(reservation.getStatus());
        reservation.setStatus(ReservationStatus.CANCELED);
        reservation.setCanceledAt(LocalDateTime.now());
        reservationMapper.updateById(reservation);
        if (holdReleased) {
            promoteNext(reservation.getBookId());
        }
    }

    public BookReservation findReady(Long bookId, Long userId) {
        return reservationMapper.selectOne(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getBookId, bookId)
                .eq(BookReservation::getUserId, userId)
                .eq(BookReservation::getStatus, ReservationStatus.READY)
                .last("LIMIT 1"));
    }

    public boolean existsReadyForOther(Long bookId, Long userId) {
        return reservationMapper.selectCount(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getBookId, bookId)
                .eq(BookReservation::getStatus, ReservationStatus.READY)
                .ne(BookReservation::getUserId, userId)) > 0;
    }

    public boolean existsActiveForOther(Long bookId, Long userId) {
        return reservationMapper.selectCount(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getBookId, bookId)
                .in(BookReservation::getStatus, ReservationStatus.ACTIVE)
                .ne(BookReservation::getUserId, userId)) > 0;
    }

    @Transactional
    public void completeOnBorrow(Long bookId, Long userId) {
        BookReservation ready = findReady(bookId, userId);
        if (ready == null) {
            return;
        }
        ready.setStatus(ReservationStatus.COMPLETED);
        ready.setCompletedAt(LocalDateTime.now());
        reservationMapper.updateById(ready);
    }

    /**
     * 图书归还或保留被释放后，把队首的排队记录提升为待取书。
     */
    @Transactional
    public void promoteNext(Long bookId) {
        long readyCount = reservationMapper.selectCount(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getBookId, bookId)
                .eq(BookReservation::getStatus, ReservationStatus.READY));
        if (readyCount > 0) {
            return;
        }
        BookReservation next = reservationMapper.selectOne(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getBookId, bookId)
                .eq(BookReservation::getStatus, ReservationStatus.WAITING)
                .orderByAsc(BookReservation::getCreatedAt)
                .orderByAsc(BookReservation::getId)
                .last("LIMIT 1"));
        if (next == null) {
            return;
        }
        BorrowRule rule = borrowRuleService.current();
        LocalDateTime now = LocalDateTime.now();
        next.setStatus(ReservationStatus.READY);
        next.setReadyAt(now);
        next.setExpireAt(now.plusDays(rule.getHoldDays()));
        reservationMapper.updateById(next);
    }

    /**
     * 释放超出保留期的待取书记录，并继续通知下一位读者。
     */
    @Transactional
    public int expireReadyReservations() {
        List<BookReservation> expired = reservationMapper.selectList(Wrappers.<BookReservation>lambdaQuery()
                .eq(BookReservation::getStatus, ReservationStatus.READY)
                .isNotNull(BookReservation::getExpireAt)
                .lt(BookReservation::getExpireAt, LocalDateTime.now()));
        for (BookReservation reservation : expired) {
            reservation.setStatus(ReservationStatus.EXPIRED);
            reservationMapper.updateById(reservation);
            promoteNext(reservation.getBookId());
        }
        return expired.size();
    }

    private BookReservation requireReservation(Long id) {
        BookReservation reservation = reservationMapper.selectById(id);
        if (reservation == null) {
            throw new BusinessException("预约记录不存在");
        }
        return reservation;
    }
}
