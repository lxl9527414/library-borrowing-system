package com.library.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.library.common.ApiResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.method.HandlerMethod;
import org.springframework.web.servlet.HandlerInterceptor;

import java.nio.charset.StandardCharsets;
import java.util.Arrays;

@Component
@RequiredArgsConstructor
public class AuthInterceptor implements HandlerInterceptor {

    private final TokenService tokenService;
    private final ObjectMapper objectMapper;

    @Override
    public boolean preHandle(HttpServletRequest request, HttpServletResponse response, Object handler)
            throws Exception {
        if (!(handler instanceof HandlerMethod)) {
            return true;
        }

        String token = extractToken(request);
        if (token == null) {
            return reject(response, 401, "请先登录");
        }

        UserSession currentUser = tokenService.resolve(token);
        if (currentUser == null) {
            return reject(response, 401, "登录已过期，请重新登录");
        }

        RequireRole requireRole = ((HandlerMethod) handler).getMethodAnnotation(RequireRole.class);
        if (requireRole == null) {
            requireRole = ((HandlerMethod) handler).getBeanType().getAnnotation(RequireRole.class);
        }
        if (requireRole != null && Arrays.stream(requireRole.value()).noneMatch(role -> role.equals(currentUser.getRole()))) {
            return reject(response, 403, "无权执行此操作");
        }

        request.setAttribute(AuthContext.CURRENT_USER_ATTRIBUTE, currentUser);
        return true;
    }

    private String extractToken(HttpServletRequest request) {
        String authorization = request.getHeader("Authorization");
        if (authorization == null || !authorization.startsWith("Bearer ")) {
            return null;
        }
        return authorization.substring(7).trim();
    }

    private boolean reject(HttpServletResponse response, int code, String message) throws Exception {
        response.setStatus(code);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        objectMapper.writeValue(response.getWriter(), ApiResponse.error(code, message));
        return false;
    }
}

