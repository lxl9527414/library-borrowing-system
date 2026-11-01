package com.library.ai.vo;

import com.library.vo.BorrowRecordVO;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.Duration;
import java.time.LocalDateTime;

/**
 * AI 工具 get_my_borrows 的返回项（契约 §2.5）。
 *
 * <p>逾期标记与罚金金额直接复用 {@link com.library.service.BorrowService#page} 已经算好的
 * {@link BorrowRecordVO}，本类不做任何规则重算；只额外把「逾期了几天」换算成展示值，
 * 方便模型向用户解释。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AiBorrowRecordVO {

    private static final long SECONDS_PER_DAY = 86_400L;

    private Long id;
    private Long bookId;
    private String bookTitle;
    private String bookIsbn;
    private String status;
    private LocalDateTime borrowedAt;
    private LocalDateTime dueAt;
    private Boolean overdue;
    private Long overdueDays;
    private Integer renewCount;
    private BigDecimal fineAmount;
    private String fineStatus;

    public static AiBorrowRecordVO from(BorrowRecordVO record) {
        if (record == null) {
            return null;
        }
        return new AiBorrowRecordVO(
                record.getId(),
                record.getBookId(),
                record.getBookTitle(),
                record.getBookIsbn(),
                record.getStatus(),
                record.getBorrowedAt(),
                record.getDueAt(),
                Boolean.TRUE.equals(record.getOverdue()),
                overdueDays(record),
                record.getRenewCount(),
                record.getFineAmount(),
                record.getFineStatus());
    }

    /**
     * 仅在现有 VO 已标记 overdue=true 的前提下换算展示天数，口径与归还罚金的「按天向上取整」一致。
     */
    private static long overdueDays(BorrowRecordVO record) {
        if (!Boolean.TRUE.equals(record.getOverdue()) || record.getDueAt() == null) {
            return 0L;
        }
        long overdueSeconds = Duration.between(record.getDueAt(), LocalDateTime.now()).getSeconds();
        return Math.max(1L, (overdueSeconds + SECONDS_PER_DAY - 1) / SECONDS_PER_DAY);
    }
}
