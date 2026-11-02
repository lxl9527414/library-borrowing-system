package com.library.common;

import java.util.List;

public final class ReservationStatus {

    public static final String WAITING = "WAITING";
    public static final String READY = "READY";
    public static final String COMPLETED = "COMPLETED";
    public static final String CANCELED = "CANCELED";
    public static final String EXPIRED = "EXPIRED";

    public static final List<String> ACTIVE = List.of(WAITING, READY);
    public static final List<String> HISTORY = List.of(COMPLETED, CANCELED, EXPIRED);

    private ReservationStatus() {
    }
}
