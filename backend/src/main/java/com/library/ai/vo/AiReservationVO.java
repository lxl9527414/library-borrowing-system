package com.library.ai.vo;

import com.library.entity.BookReservation;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * AI 图书详情里「我的预约」快照（契约 §2.4），无预约时为 null。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AiReservationVO {

    private Long id;
    private Long bookId;
    private String status;
    private LocalDateTime readyAt;
    private LocalDateTime expireAt;

    public static AiReservationVO from(BookReservation reservation) {
        if (reservation == null) {
            return null;
        }
        return new AiReservationVO(
                reservation.getId(),
                reservation.getBookId(),
                reservation.getStatus(),
                reservation.getReadyAt(),
                reservation.getExpireAt());
    }
}
