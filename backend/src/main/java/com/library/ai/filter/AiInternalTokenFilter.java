package com.library.ai.filter;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.library.common.ApiResponse;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.MediaType;
import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * AI 内部工具接口的调用方鉴权过滤器。
 *
 * <p>与用户身份鉴权（{@link com.library.security.AuthInterceptor}）互补：
 * 本过滤器只回答「调用方是不是 AI 服务」，用户身份仍由 Authorization token 决定。
 *
 * <p>规则（契约 §1/§2）：
 * <ul>
 *     <li>只处理 {@code app.ai.internal-path-prefix} 前缀（默认 /api/ai/internal/）的请求；</li>
 *     <li>{@code app.ai.internal-token} 为空串表示开发期不校验，直接放行；</li>
 *     <li>否则要求浏览器/AI 服务透传的 {@code X-Internal-Token} 与配置完全一致，
 *         缺失或不一致返回 HTTP 403 + 统一 {@link ApiResponse} 结构。</li>
 * </ul>
 */
@Slf4j
@Component
@RequiredArgsConstructor
public class AiInternalTokenFilter extends OncePerRequestFilter {

    /** AI 服务与 Java 层约定的内部密钥请求头。 */
    public static final String INTERNAL_TOKEN_HEADER = "X-Internal-Token";

    private static final int FORBIDDEN_CODE = 403;
    private static final String FORBIDDEN_MESSAGE = "内部接口鉴权失败";

    private final ObjectMapper objectMapper;

    @Value("${app.ai.internal-token:}")
    private String internalToken;

    @Value("${app.ai.internal-path-prefix:/api/ai/internal/}")
    private String internalPathPrefix;

    @Override
    protected boolean shouldNotFilter(HttpServletRequest request) {
        return !isInternalRequest(request);
    }

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain filterChain)
            throws ServletException, IOException {
        if (internalToken == null || internalToken.isBlank()) {
            // 开发期未配置内部密钥：按契约放行，避免本地联调被卡住
            log.debug("未配置 app.ai.internal-token，跳过内部接口密钥校验（仅限开发环境）");
            filterChain.doFilter(request, response);
            return;
        }
        if (matches(request.getHeader(INTERNAL_TOKEN_HEADER))) {
            filterChain.doFilter(request, response);
            return;
        }
        String sourceIp = resolveIp(request);
        log.warn("AI 内部接口鉴权失败：来源 IP={}，method={}，uri={}",
                sourceIp, request.getMethod(), request.getRequestURI());
        writeForbidden(response);
    }

    /**
     * 断言请求路径命中内部接口前缀，兼容 context-path 以及不带末尾斜杠的写法。
     */
    private boolean isInternalRequest(HttpServletRequest request) {
        String prefix = internalPathPrefix;
        if (prefix == null || prefix.isBlank()) {
            return false;
        }
        if (!prefix.endsWith("/")) {
            prefix = prefix + "/";
        }
        String path = request.getRequestURI();
        String contextPath = request.getContextPath();
        if (contextPath != null && !contextPath.isEmpty() && path.startsWith(contextPath)) {
            path = path.substring(contextPath.length());
        }
        return path.equals(prefix.substring(0, prefix.length() - 1)) || path.startsWith(prefix);
    }

    /**
     * 常量时间比较，避免通过响应耗时逐字符试探内部密钥。
     */
    private boolean matches(String provided) {
        if (provided == null || provided.isBlank()) {
            return false;
        }
        byte[] expected = internalToken.getBytes(StandardCharsets.UTF_8);
        byte[] actual = provided.trim().getBytes(StandardCharsets.UTF_8);
        return MessageDigest.isEqual(expected, actual);
    }

    private void writeForbidden(HttpServletResponse response) throws IOException {
        response.setStatus(FORBIDDEN_CODE);
        response.setCharacterEncoding(StandardCharsets.UTF_8.name());
        response.setContentType(MediaType.APPLICATION_JSON_VALUE);
        objectMapper.writeValue(response.getWriter(), ApiResponse.error(FORBIDDEN_CODE, FORBIDDEN_MESSAGE));
    }

    private String resolveIp(HttpServletRequest request) {
        String forwarded = request.getHeader("X-Forwarded-For");
        if (forwarded != null && !forwarded.isBlank()) {
            return forwarded.split(",")[0].trim();
        }
        return request.getRemoteAddr();
    }
}
