package com.library.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Pattern;
import jakarta.validation.constraints.Size;
import lombok.Data;

@Data
public class UserSaveRequest {

    private Long id;

    @NotBlank(message = "请输入用户名")
    @Size(max = 50, message = "用户名不能超过 50 个字符")
    private String username;

    @Size(max = 50, message = "姓名不能超过 50 个字符")
    private String realName;

    @Pattern(regexp = "ADMIN|READER", message = "角色值不正确")
    private String role;

    private Integer status;

    @Size(min = 6, max = 30, message = "密码长度应为 6-30 位")
    private String password;
}

