package com.library.controller;

import com.library.common.ApiResponse;
import com.library.dto.LoginRequest;
import com.library.dto.LoginResponse;
import com.library.security.AuthContext;
import com.library.security.TokenService;
import com.library.security.UserSession;
import com.library.service.AuthService;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/auth")
@RequiredArgsConstructor
public class AuthController {

    private final AuthService authService;
    private final TokenService tokenService;

    @PostMapping("/login")
    public ApiResponse<LoginResponse> login(@Valid @RequestBody LoginRequest request) {
        return ApiResponse.ok(authService.login(request));
    }

    @GetMapping("/me")
    public ApiResponse<UserSession> me(@RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(currentUser);
    }

    @PostMapping("/logout")
    public ApiResponse<Void> logout(HttpServletRequest request) {
        String authorization = request.getHeader("Authorization");
        if (authorization != null && authorization.startsWith("Bearer ")) {
            tokenService.revoke(authorization.substring(7).trim());
        }
        return ApiResponse.ok();
    }
}

