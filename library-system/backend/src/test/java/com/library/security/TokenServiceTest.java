package com.library.security;

import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.data.redis.core.ValueOperations;
import org.springframework.test.util.ReflectionTestUtils;

import java.time.Duration;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class TokenServiceTest {

    @Mock
    private StringRedisTemplate redisTemplate;
    @Mock
    private ValueOperations<String, String> valueOperations;

    private TokenService tokenService;

    @BeforeEach
    void setUp() {
        tokenService = new TokenService(redisTemplate, new ObjectMapper());
        ReflectionTestUtils.setField(tokenService, "tokenPrefix", "library:token:");
        ReflectionTestUtils.setField(tokenService, "tokenTtl", 604800L);
    }

    @Test
    @DisplayName("登录态：生成的 Token 为 32 位随机串并带 7 天 TTL 写入 Redis")
    void create_storesSessionWithTtl() {
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        UserSession session = new UserSession(2L, "reader", "普通读者", "READER");

        String token = tokenService.create(session);

        assertThat(token).hasSize(32);
        ArgumentCaptor<String> keyCaptor = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> jsonCaptor = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<Duration> ttlCaptor = ArgumentCaptor.forClass(Duration.class);
        verify(valueOperations).set(keyCaptor.capture(), jsonCaptor.capture(), ttlCaptor.capture());
        assertThat(keyCaptor.getValue()).isEqualTo("library:token:" + token);
        assertThat(jsonCaptor.getValue()).contains("\"username\":\"reader\"");
        assertThat(ttlCaptor.getValue()).isEqualTo(Duration.ofSeconds(604800));
    }

    @Test
    @DisplayName("登录态：Token 能还原出会话信息")
    void resolve_returnsSession() {
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.get("library:token:abc"))
                .thenReturn("{\"id\":2,\"username\":\"reader\",\"realName\":\"普通读者\",\"role\":\"READER\"}");

        UserSession session = tokenService.resolve("abc");

        assertThat(session).isNotNull();
        assertThat(session.getId()).isEqualTo(2L);
        assertThat(session.getRole()).isEqualTo("READER");
    }

    @Test
    @DisplayName("登录态：Redis 中没有 Token 时返回 null（未登录/已过期）")
    void resolve_whenTokenMissing_thenNull() {
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.get("library:token:missing")).thenReturn(null);

        assertThat(tokenService.resolve("missing")).isNull();
    }

    @Test
    @DisplayName("登录态：Token 为空时直接返回 null，不查 Redis")
    void resolve_whenBlankToken_thenNull() {
        assertThat(tokenService.resolve("  ")).isNull();
    }

    @Test
    @DisplayName("登录态：会话 JSON 损坏时删除脏数据并返回 null")
    void resolve_whenJsonBroken_thenDeleteKey() {
        when(redisTemplate.opsForValue()).thenReturn(valueOperations);
        when(valueOperations.get("library:token:broken")).thenReturn("{not-json");

        assertThat(tokenService.resolve("broken")).isNull();
        verify(redisTemplate).delete("library:token:broken");
    }

    @Test
    @DisplayName("退出登录：删除 Redis 中的 Token，实现立即失效")
    void revoke_deletesToken() {
        tokenService.revoke("abc");

        verify(redisTemplate).delete("library:token:abc");
    }
}
