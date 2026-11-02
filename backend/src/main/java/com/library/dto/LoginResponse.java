package com.library.dto;

import com.library.security.UserSession;
import lombok.AllArgsConstructor;
import lombok.Data;

@Data
@AllArgsConstructor
public class LoginResponse {

    private String token;
    private UserSession user;
}

