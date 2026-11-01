package com.library.ai.dto;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * AI 工具 reserve_book 的请求体：只接收图书 ID。
 *
 * <p>预约人身份由 {@link com.library.security.AuthContext} 中解析出的当前登录用户决定。
 */
@Data
public class AiReserveRequest {

    @NotNull(message = "请选择要预约的图书")
    private Long bookId;
}
