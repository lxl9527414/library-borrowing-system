package com.library.dto;

import jakarta.validation.constraints.NotNull;
import lombok.Data;

@Data
public class ReservationRequest {

    @NotNull(message = "请选择要预约的图书")
    private Long bookId;
}
