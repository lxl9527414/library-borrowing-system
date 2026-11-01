package com.library.ai.vo;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * AI 图书详情（契约 §2.4）：图书摘要 + 预约队列长度 + 我的有效预约。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AiBookDetailVO {

    private AiBookVO book;
    private long waitingCount;
    private AiReservationVO myReservation;
}
