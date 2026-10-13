package com.library.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.library.common.BusinessException;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.UUID;

@Service
@RequiredArgsConstructor
public class TokenService {

    private final StringRedisTemplate redisTemplate;
    private final ObjectMapper objectMapper;

    @Value("${app.auth.token-prefix:library:token:}")
    private String tokenPrefix;

    @Value("${app.auth.token-ttl:604800}")
    private long tokenTtl;

    public String create(UserSession session) {
        String token = UUID.randomUUID().toString().replace("-", "");
        try {
            String json = objectMapper.writeValueAsString(session);
            redisTemplate.opsForValue().set(tokenPrefix + token, json, Duration.ofSeconds(tokenTtl));
            return token;
        } catch (Exception ex) {
            throw new BusinessException(500, "登录状态保存失败，请检查 Redis 服务");
        }
    }

    public UserSession resolve(String token) {
        if (token == null || token.isBlank()) {
            return null;
        }
        String json = redisTemplate.opsForValue().get(tokenPrefix + token);
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return objectMapper.readValue(json, UserSession.class);
        } catch (Exception ex) {
            redisTemplate.delete(tokenPrefix + token);
            return null;
        }
    }

    public void revoke(String token) {
        if (token != null && !token.isBlank()) {
            redisTemplate.delete(tokenPrefix + token);
        }
    }
}

