package com.library.controller;

import com.library.common.ApiResponse;
import com.library.common.PageResult;
import com.library.dto.ResetPasswordRequest;
import com.library.dto.UserSaveRequest;
import com.library.entity.User;
import com.library.log.Auditable;
import com.library.security.AuthContext;
import com.library.security.RequireRole;
import com.library.security.UserSession;
import com.library.service.UserService;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.DeleteMapping;
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
@RequestMapping("/api/users")
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    @RequireRole("ADMIN")
    @GetMapping
    public ApiResponse<PageResult<User>> page(
            @RequestParam(defaultValue = "1") long current,
            @RequestParam(defaultValue = "10") long size,
            @RequestParam(required = false) String keyword) {
        return ApiResponse.ok(PageResult.of(userService.page(current, size, keyword)));
    }

    @RequireRole("ADMIN")
    @PostMapping
    @Auditable(module = "用户", action = "新增用户")
    public ApiResponse<User> create(@Valid @RequestBody UserSaveRequest request) {
        return ApiResponse.ok(userService.create(request));
    }

    @RequireRole("ADMIN")
    @PutMapping("/{id}")
    @Auditable(module = "用户", action = "修改用户")
    public ApiResponse<User> update(
            @PathVariable Long id,
            @Valid @RequestBody UserSaveRequest request,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        request.setId(id);
        return ApiResponse.ok(userService.update(request, currentUser));
    }

    @RequireRole("ADMIN")
    @PutMapping("/{id}/password")
    @Auditable(module = "用户", action = "重置密码")
    public ApiResponse<Void> resetPassword(
            @PathVariable Long id,
            @Valid @RequestBody ResetPasswordRequest request) {
        userService.resetPassword(id, request.getPassword());
        return ApiResponse.ok();
    }

    @RequireRole("ADMIN")
    @DeleteMapping("/{id}")
    @Auditable(module = "用户", action = "删除用户")
    public ApiResponse<Void> delete(
            @PathVariable Long id,
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        userService.delete(id, currentUser);
        return ApiResponse.ok();
    }
}
