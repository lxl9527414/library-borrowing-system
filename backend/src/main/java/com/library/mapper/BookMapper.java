package com.library.mapper;

import com.baomidou.mybatisplus.core.mapper.BaseMapper;
import com.library.entity.Book;
import com.library.vo.DashboardStatVO;

import java.util.List;

public interface BookMapper extends BaseMapper<Book> {

    List<DashboardStatVO> selectCategoryDistribution();
}

