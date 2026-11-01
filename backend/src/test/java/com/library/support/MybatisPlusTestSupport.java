package com.library.support;

import com.baomidou.mybatisplus.core.MybatisConfiguration;
import com.baomidou.mybatisplus.core.metadata.TableInfoHelper;
import org.apache.ibatis.builder.MapperBuilderAssistant;

import java.util.HashSet;
import java.util.Set;

/**
 * 纯单元测试里没有 MyBatis 上下文，LambdaQueryWrapper 的 in() 无法解析列名。
 * 这里手动初始化实体 TableInfo 缓存，保证 Service 层条件构造器可用。
 */
public final class MybatisPlusTestSupport {

    private static final Set<Class<?>> INITIALIZED = new HashSet<>();

    private MybatisPlusTestSupport() {
    }

    public static synchronized void initTableInfo(Class<?>... entities) {
        MapperBuilderAssistant assistant = new MapperBuilderAssistant(new MybatisConfiguration(), "");
        for (Class<?> entity : entities) {
            if (INITIALIZED.add(entity)) {
                TableInfoHelper.initTableInfo(assistant, entity);
            }
        }
    }
}
