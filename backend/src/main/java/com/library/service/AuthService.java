package com.library.service;

import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.library.common.BusinessException;
import com.library.dto.LoginRequest;
import com.library.dto.LoginResponse;
import com.library.entity.User;
import com.library.mapper.UserMapper;
import com.library.security.TokenService;
import com.library.security.UserSession;
import lombok.RequiredArgsConstructor;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class AuthService {

    private final UserMapper userMapper;
    private final BCryptPasswordEncoder passwordEncoder;
    private final TokenService tokenService;

    public LoginResponse login(LoginRequest request) {
        User user = userMapper.selectOne(Wrappers.<User>lambdaQuery()
                .eq(User::getUsername, request.getUsername().trim())
                .last("LIMIT 1"));
        if (user == null || !passwordEncoder.matches(request.getPassword(), user.getPassword())) {
            throw new BusinessException(400, "用户名或密码错误");
        }
        if (!Integer.valueOf(1).equals(user.getStatus())) {
            throw new BusinessException(403, "账号已被停用，请联系管理员");
        }

        UserSession session = UserSession.from(user);
        return new LoginResponse(tokenService.create(session), session);
    }
}

