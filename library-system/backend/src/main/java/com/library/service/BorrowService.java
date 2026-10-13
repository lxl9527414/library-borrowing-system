package com.library.service;

import cn.hutool.core.util.IdUtil;
import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.library.common.BusinessException;
import com.library.common.FineStatus;
import com.library.entity.Book;
import com.library.entity.BookReservation;
import com.library.entity.BorrowRecord;
import com.library.entity.BorrowRule;
import com.library.entity.Role;
import com.library.mapper.BookMapper;
import com.library.mapper.BorrowRecordMapper;
import com.library.security.UserSession;
import com.library.vo.BorrowRecordVO;
import lombok.RequiredArgsConstructor;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.LocalDateTime;

@Service
@RequiredArgsConstructor
public class BorrowService {

    private static final String STATUS_BORROWED = "BORROWED";
    private static final String STATUS_RETURNED = "RETURNED";
    private static final long SECONDS_PER_DAY = 86_400L;

    private final BookMapper bookMapper;
    private final BorrowRecordMapper borrowRecordMapper;
    private final BorrowRuleService borrowRuleService;
    private final ReservationService reservationService;

    @Transactional
    public BorrowRecord borrow(Long bookId, UserSession currentUser) {
        Book book = bookMapper.selectById(bookId);
        if (book == null) {
            throw new BusinessException("图书不存在");
        }

        BorrowRule rule = borrowRuleService.current();
        long activeCount = borrowRecordMapper.selectCount(Wrappers.<BorrowRecord>lambdaQuery()
                .eq(BorrowRecord::getUserId, currentUser.getId())
                .eq(BorrowRecord::getStatus, STATUS_BORROWED));
        if (activeCount >= rule.getMaxBorrowCount()) {
            throw new BusinessException("同时借阅上限为 " + rule.getMaxBorrowCount() + " 本，请先归还后再借");
        }

        long sameBook = borrowRecordMapper.selectCount(Wrappers.<BorrowRecord>lambdaQuery()
                .eq(BorrowRecord::getUserId, currentUser.getId())
                .eq(BorrowRecord::getBookId, bookId)
                .eq(BorrowRecord::getStatus, STATUS_BORROWED));
        if (sameBook > 0) {
            throw new BusinessException("您已借阅该图书且尚未归还");
        }

        BookReservation ready = reservationService.findReady(bookId, currentUser.getId());
        if (ready == null) {
            if (reservationService.existsReadyForOther(bookId, currentUser.getId())) {
                throw new BusinessException("该书已被其他读者预约保留，暂不可借");
            }
            if (book.getAvailableStock() == null || book.getAvailableStock() <= 0) {
                throw new BusinessException("该图书暂无可借库存，可预约排队");
            }
        }

        int updated = bookMapper.update(null, Wrappers.<Book>lambdaUpdate()
                .eq(Book::getId, bookId)
                .gt(Book::getAvailableStock, 0)
                .setSql("available_stock = available_stock - 1"));
        if (updated == 0) {
            throw new BusinessException("图书库存刚刚发生变化，请稍后重试");
        }

        LocalDateTime now = LocalDateTime.now();
        BorrowRecord record = new BorrowRecord();
        record.setBorrowNo("BR" + IdUtil.getSnowflakeNextIdStr());
        record.setUserId(currentUser.getId());
        record.setBookId(book.getId());
        record.setBookTitle(book.getTitle());
        record.setBookIsbn(book.getIsbn());
        record.setStatus(STATUS_BORROWED);
        record.setBorrowedAt(now);
        record.setDueAt(now.plusDays(rule.getBorrowDays()));
        record.setRenewCount(0);
        record.setFineAmount(BigDecimal.ZERO.setScale(2, RoundingMode.HALF_UP));
        record.setFineStatus(FineStatus.NONE);
        borrowRecordMapper.insert(record);

        reservationService.completeOnBorrow(bookId, currentUser.getId());
        return record;
    }

    @Transactional
    public BorrowRecord returnBook(Long recordId, UserSession currentUser) {
        BorrowRecord record = requireBorrowRecord(recordId, currentUser, "只能归还自己的借阅记录");
        if (!STATUS_BORROWED.equals(record.getStatus())) {
            throw new BusinessException("该记录已完成归还");
        }

        LocalDateTime now = LocalDateTime.now();
        record.setStatus(STATUS_RETURNED);
        record.setReturnedAt(now);
        applyFine(record, now);
        borrowRecordMapper.updateById(record);

        bookMapper.update(null, Wrappers.<Book>lambdaUpdate()
                .eq(Book::getId, record.getBookId())
                .setSql("available_stock = LEAST(available_stock + 1, total_stock)"));

        reservationService.promoteNext(record.getBookId());
        return record;
    }

    /**
     * 续借：逾期记录、达到续借上限或已有其他读者预约时不允许续借。
     */
    @Transactional
    public BorrowRecord renew(Long recordId, UserSession currentUser) {
        BorrowRecord record = requireBorrowRecord(recordId, currentUser, "只能续借自己的借阅记录");
        if (!STATUS_BORROWED.equals(record.getStatus())) {
            throw new BusinessException("该记录已完成归还");
        }
        BorrowRule rule = borrowRuleService.current();
        if (rule.getMaxRenewCount() == null || rule.getMaxRenewCount() <= 0) {
            throw new BusinessException("当前借阅规则不允许续借");
        }
        LocalDateTime now = LocalDateTime.now();
        if (record.getDueAt() == null || record.getDueAt().isBefore(now)) {
            throw new BusinessException("该记录已逾期，请先归还并缴纳罚金");
        }
        int renewCount = record.getRenewCount() == null ? 0 : record.getRenewCount();
        if (renewCount >= rule.getMaxRenewCount()) {
            throw new BusinessException("最多可续借 " + rule.getMaxRenewCount() + " 次");
        }
        if (reservationService.existsActiveForOther(record.getBookId(), record.getUserId())) {
            throw new BusinessException("该书已有读者预约，不能续借");
        }

        record.setRenewCount(renewCount + 1);
        record.setDueAt(record.getDueAt().plusDays(rule.getRenewDays()));
        borrowRecordMapper.updateById(record);
        return record;
    }

    /**
     * 归还时按规则结算逾期罚金：按天向上取整，并受罚金上限约束。
     */
    private void applyFine(BorrowRecord record, LocalDateTime now) {
        if (record.getDueAt() == null || !now.isAfter(record.getDueAt())) {
            record.setFineAmount(BigDecimal.ZERO.setScale(2, RoundingMode.HALF_UP));
            record.setFineStatus(FineStatus.NONE);
            return;
        }
        BorrowRule rule = borrowRuleService.current();
        long overdueSeconds = Duration.between(record.getDueAt(), now).getSeconds();
        long overdueDays = Math.max(1, (overdueSeconds + SECONDS_PER_DAY - 1) / SECONDS_PER_DAY);

        BigDecimal fine = rule.getFinePerDay().multiply(BigDecimal.valueOf(overdueDays));
        if (rule.getFineMax() != null
                && rule.getFineMax().compareTo(BigDecimal.ZERO) > 0
                && fine.compareTo(rule.getFineMax()) > 0) {
            fine = rule.getFineMax();
        }
        fine = fine.setScale(2, RoundingMode.HALF_UP);
        record.setFineAmount(fine);
        record.setFineStatus(fine.compareTo(BigDecimal.ZERO) > 0 ? FineStatus.UNPAID : FineStatus.NONE);
    }

    @Transactional
    public BorrowRecord settleFine(Long recordId) {
        BorrowRecord record = borrowRecordMapper.selectById(recordId);
        if (record == null) {
            throw new BusinessException("借阅记录不存在");
        }
        if (!FineStatus.UNPAID.equals(record.getFineStatus())) {
            throw new BusinessException("该记录没有待缴纳的罚金");
        }
        record.setFineStatus(FineStatus.PAID);
        borrowRecordMapper.updateById(record);
        return record;
    }

    public IPage<BorrowRecordVO> page(long current, long size, String status, String keyword, UserSession currentUser) {
        Long userId = Role.ADMIN.name().equals(currentUser.getRole()) ? null : currentUser.getId();
        IPage<BorrowRecordVO> page = borrowRecordMapper.selectRecordPage(
                new Page<>(current, size), userId, status, keyword);
        LocalDateTime now = LocalDateTime.now();
        page.getRecords().forEach(record -> record.setOverdue(
                STATUS_BORROWED.equals(record.getStatus())
                        && record.getDueAt() != null
                        && record.getDueAt().isBefore(now)));
        return page;
    }

    private BorrowRecord requireBorrowRecord(Long recordId, UserSession currentUser, String forbiddenMessage) {
        BorrowRecord record = borrowRecordMapper.selectById(recordId);
        if (record == null) {
            throw new BusinessException("借阅记录不存在");
        }
        if (!Role.ADMIN.name().equals(currentUser.getRole())
                && !currentUser.getId().equals(record.getUserId())) {
            throw new BusinessException(403, forbiddenMessage);
        }
        return record;
    }
}
