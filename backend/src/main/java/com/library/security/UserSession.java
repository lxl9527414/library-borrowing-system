package com.library.security;

import com.library.entity.User;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

@Data
@NoArgsConstructor
@AllArgsConstructor
public class UserSession {

    private Long id;
    private String username;
    private String realName;
    private String role;

    public static UserSession from(User user) {
        return new UserSession(user.getId(), user.getUsername(), user.getRealName(), user.getRole());
    }
}

