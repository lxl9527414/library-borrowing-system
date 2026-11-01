package com.library.dto;

import jakarta.validation.constraints.Size;
import lombok.Data;

@Data
public class ResetPasswordRequest {

    @Size(min = 6, max = 30, message = "密码长度应为 6-30 位")
    private String password;
}

