package com.library.service;

import com.baomidou.mybatisplus.core.metadata.IPage;
import com.baomidou.mybatisplus.core.toolkit.Wrappers;
import com.baomidou.mybatisplus.extension.plugins.pagination.Page;
import com.library.common.BusinessException;
import com.library.dto.UserSaveRequest;
import com.library.entity.Role;
import com.library.entity.User;
import com.library.mapper.UserMapper;
import com.library.security.UserSession;
import lombok.RequiredArgsConstructor;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.util.StringUtils;

@Service
@RequiredArgsConstructor
public class UserService {

    private final UserMapper userMapper;
    private final BCryptPasswordEncoder passwordEncoder;

    public IPage<User> page(long current, long size, String keyword) {
        return userMapper.selectPage(
                new Page<>(current, size),
                Wrappers.<User>lambdaQuery()
                        .and(StringUtils.hasText(keyword), wrapper -> wrapper
                                .like(User::getUsername, keyword)
                                .or()
                                .like(User::getRealName, keyword))
                        .orderByDesc(User::getCreatedAt)
        );
    }

    @Transactional
    public User create(UserSaveRequest request) {
        validateRole(request.getRole());
        String username = request.getUsername().trim();
        assertUsernameAvailable(username, null);

        User user = new User();
        user.setUsername(username);
        user.setRealName(StringUtils.hasText(request.getRealName()) ? request.getRealName().trim() : username);
        user.setRole(request.getRole());
        user.setStatus(request.getStatus() == null ? 1 : request.getStatus());
        user.setPassword(passwordEncoder.encode(StringUtils.hasText(request.getPassword())
                ? request.getPassword() : "123456"));
        userMapper.insert(user);
        return user;
    }

    @Transactional
    public User update(UserSaveRequest request, UserSession operator) {
        if (request.getId() == null) {
            throw new BusinessException("用户 ID 不能为空");
        }
        User user = requireUser(request.getId());
        validateRole(request.getRole());
        String username = request.getUsername().trim();
        assertUsernameAvailable(username, request.getId());

        if (operator.getId().equals(user.getId())) {
            if (!"ADMIN".equals(request.getRole()) || !Integer.valueOf(1).equals(request.getStatus())) {
                throw new BusinessException("不能停用自己或修改自己的角色");
            }
        }

        user.setUsername(username);
        user.setRealName(StringUtils.hasText(request.getRealName()) ? request.getRealName().trim() : username);
        user.setRole(request.getRole());
        user.setStatus(request.getStatus() == null ? 1 : request.getStatus());
        if (StringUtils.hasText(request.getPassword())) {
            user.setPassword(passwordEncoder.encode(request.getPassword()));
        }
        userMapper.updateById(user);
        return user;
    }

    @Transactional
    public void resetPassword(Long id, String password) {
        User user = requireUser(id);
        String newPassword = StringUtils.hasText(password) ? password : "123456";
        user.setPassword(passwordEncoder.encode(newPassword));
        userMapper.updateById(user);
    }

    @Transactional
    public void delete(Long id, UserSession operator) {
        if (operator.getId().equals(id)) {
            throw new BusinessException("不能删除当前登录账号");
        }
        User user = requireUser(id);
        if ("ADMIN".equals(user.getRole())) {
            long adminCount = userMapper.selectCount(Wrappers.<User>lambdaQuery()
                    .eq(User::getRole, Role.ADMIN.name())
                    .eq(User::getStatus, 1));
            if (adminCount <= 1) {
                throw new BusinessException("系统至少需要保留一个启用状态的管理员");
            }
        }
        userMapper.deleteById(id);
    }

    private User requireUser(Long id) {
        User user = userMapper.selectById(id);
        if (user == null) {
            throw new BusinessException("用户不存在");
        }
        return user;
    }

    private void assertUsernameAvailable(String username, Long excludeId) {
        Long count = userMapper.selectCount(Wrappers.<User>lambdaQuery()
                .eq(User::getUsername, username)
                .ne(excludeId != null, User::getId, excludeId));
        if (count > 0) {
            throw new BusinessException("用户名已存在");
        }
    }

    private void validateRole(String role) {
        try {
            Role.valueOf(role);
        } catch (Exception ex) {
            throw new BusinessException("角色值不正确");
        }
    }
}

