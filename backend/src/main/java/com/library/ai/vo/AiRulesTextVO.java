package com.library.ai.vo;

import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * 借阅规则自然语言化结果（契约 §2.2），由服务端注入 prompt，不经过 LLM 工具。
 */
@Data
@NoArgsConstructor
@AllArgsConstructor
public class AiRulesTextVO {

    private String text;
}
