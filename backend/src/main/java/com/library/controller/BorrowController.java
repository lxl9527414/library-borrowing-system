package com.library.controller;

import com.library.common.ApiResponse;
import com.library.common.PageResult;
import com.library.dto.BorrowRequest;
import com.library.entity.BorrowRecord;
import com.library.log.Auditable;
import com.library.security.AuthContext;
import com.library.security.RequireRole;
import com.library.security.UserSession;
import com.library.service.BorrowService;
import com.library.vo.BorrowRecordVO;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/borrows")
@RequiredArgsConstructor
public class BorrowController {

    private final BorrowService borrowService;

    @GetMapping
    public ApiResponse<PageResult<BorrowRecordVO>> page(
            @RequestParam(defaultValue = "1") long current,
            @RequestParam(defaultValue = "10") long size,
            @RequestParam(required = false) String status,
            @RequestParam(required = false) String keyword,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(PageResult.of(borrowService.page(current, size, status, keyword, currentUser)));
    }

    @PostMapping
    @Auditable(module = "借阅", action = "借阅图书")
    public ApiResponse<BorrowRecord> borrow(
            @Valid @RequestBody BorrowRequest request,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(borrowService.borrow(request.getBookId(), currentUser));
    }

    @PutMapping("/{id}/return")
    @Auditable(module = "借阅", action = "归还图书")
    public ApiResponse<BorrowRecord> returnBook(
            @PathVariable Long id,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(borrowService.returnBook(id, currentUser));
    }

    @PutMapping("/{id}/renew")
    @Auditable(module = "借阅", action = "续借图书")
    public ApiResponse<BorrowRecord> renew(
            @PathVariable Long id,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(borrowService.renew(id, currentUser));
    }

    @RequireRole("ADMIN")
    @PutMapping("/{id}/fine/settle")
    @Auditable(module = "借阅", action = "罚金缴纳")
    public ApiResponse<BorrowRecord> settleFine(@PathVariable Long id) {
        return ApiResponse.ok(borrowService.settleFine(id));
    }
}
