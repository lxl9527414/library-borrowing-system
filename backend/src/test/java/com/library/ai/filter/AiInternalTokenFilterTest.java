package com.library.ai.filter;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;
import org.springframework.mock.web.MockFilterChain;
import org.springframework.mock.web.MockHttpServletRequest;
import org.springframework.mock.web.MockHttpServletResponse;
import org.springframework.test.util.ReflectionTestUtils;

import static org.assertj.core.api.Assertions.assertThat;

class AiInternalTokenFilterTest {

    private static final String INTERNAL_URI = "/api/ai/internal/context";
    private static final String INTERNAL_TOKEN = "ai-internal-secret";

    private final ObjectMapper objectMapper = new ObjectMapper();

    private AiInternalTokenFilter filter;

    @BeforeEach
    void setUp() {
        filter = new AiInternalTokenFilter(objectMapper);
        ReflectionTestUtils.setField(filter, "internalToken", INTERNAL_TOKEN);
        ReflectionTestUtils.setField(filter, "internalPathPrefix", "/api/ai/internal/");
    }

    @Test
    @DisplayName("内部密钥：缺失 X-Internal-Token 时返回 403 统一响应，且不进入业务链")
    void filter_whenHeaderMissing_thenForbidden() throws Exception {
        MockFilterChain chain = new MockFilterChain();

        MockHttpServletResponse response = doFilter(request(INTERNAL_URI), chain);

        assertThat(response.getStatus()).isEqualTo(403);
        JsonNode body = objectMapper.readTree(response.getContentAsString());
        assertThat(body.path("code").asInt()).isEqualTo(403);
        assertThat(body.path("message").asText()).isEqualTo("内部接口鉴权失败");
        assertThat(chain.getRequest()).isNull();
    }

    @Test
    @DisplayName("内部密钥：密钥错误时返回 403 统一响应，且不进入业务链")
    void filter_whenTokenWrong_thenForbidden() throws Exception {
        MockHttpServletRequest request = request(INTERNAL_URI);
        request.addHeader(AiInternalTokenFilter.INTERNAL_TOKEN_HEADER, "forged-token");
        MockFilterChain chain = new MockFilterChain();

        MockHttpServletResponse response = doFilter(request, chain);

        assertThat(response.getStatus()).isEqualTo(403);
        assertThat(objectMapper.readTree(response.getContentAsString()).path("code").asInt()).isEqualTo(403);
        assertThat(chain.getRequest()).isNull();
    }

    @Test
    @DisplayName("内部密钥：密钥正确时放行到后续业务链")
    void filter_whenTokenMatches_thenPassThrough() throws Exception {
        MockHttpServletRequest request = request(INTERNAL_URI);
        request.addHeader(AiInternalTokenFilter.INTERNAL_TOKEN_HEADER, INTERNAL_TOKEN);
        MockFilterChain chain = new MockFilterChain();

        MockHttpServletResponse response = doFilter(request, chain);

        assertThat(response.getStatus()).isEqualTo(200);
        assertThat(chain.getRequest()).isSameAs(request);
    }

    @Test
    @DisplayName("内部密钥：配置为空串（开发期）时即使不带请求头也放行")
    void filter_whenInternalTokenBlank_thenPassThrough() throws Exception {
        ReflectionTestUtils.setField(filter, "internalToken", "");
        MockFilterChain chain = new MockFilterChain();

        MockHttpServletResponse response = doFilter(request(INTERNAL_URI), chain);

        assertThat(response.getStatus()).isEqualTo(200);
        assertThat(chain.getRequest()).isNotNull();
    }

    @Test
    @DisplayName("内部密钥：非内部接口路径即使密钥错误也不拦截，避免影响现有业务接口")
    void filter_whenNotInternalPath_thenPassThrough() throws Exception {
        MockHttpServletRequest request = request("/api/books");
        request.addHeader(AiInternalTokenFilter.INTERNAL_TOKEN_HEADER, "forged-token");
        MockFilterChain chain = new MockFilterChain();

        MockHttpServletResponse response = doFilter(request, chain);

        assertThat(response.getStatus()).isEqualTo(200);
        assertThat(chain.getRequest()).isSameAs(request);
    }

    private MockHttpServletRequest request(String uri) {
        return new MockHttpServletRequest("GET", uri);
    }

    private MockHttpServletResponse doFilter(MockHttpServletRequest request, MockFilterChain chain) throws Exception {
        MockHttpServletResponse response = new MockHttpServletResponse();
        filter.doFilter(request, response, chain);
        return response;
    }
}
