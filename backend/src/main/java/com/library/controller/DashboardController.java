package com.library.controller;

import com.library.common.ApiResponse;
import com.library.security.AuthContext;
import com.library.security.UserSession;
import com.library.service.DashboardService;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestAttribute;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

@RestController
@RequestMapping("/api/dashboard")
@RequiredArgsConstructor
public class DashboardController {

    private final DashboardService dashboardService;

    @GetMapping
    public ApiResponse<Map<String, Object>> overview(
            @RequestAttribute(AuthContext.CURRENT_USER_ATTRIBUTE) UserSession currentUser) {
        return ApiResponse.ok(dashboardService.overview(currentUser));
    }
}

