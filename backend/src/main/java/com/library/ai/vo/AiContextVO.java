package com.library.ai.vo;

import com.library.security.UserSession;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * AI 会话上下文（契约 §2.1）：用户身份 + 借阅规则 + 4 个服务端统计值。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AiContextVO {

    private UserSession user;
    private AiRuleVO rule;
    private long borrowedCount;
    private long overdueCount;
    private long activeReservationCount;
    private long pendingPickupCount;
}
