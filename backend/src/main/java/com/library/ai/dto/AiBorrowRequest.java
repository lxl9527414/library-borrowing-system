package com.library.ai.dto;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

/**
 * AI 工具 borrow_book 的请求体：只接收图书 ID。
 *
 * <p>刻意不包含 userId 字段：调用人身份只能来自 Authorization token 解析出的
 * {@link com.library.security.UserSession}，LLM 无法通过请求体伪造用户。
 */
@Data
public class AiBorrowRequest {

    @NotNull(message = "请选择要借阅的图书")
    private Long bookId;
}
