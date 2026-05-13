package com.demo.banking.exception;

import java.math.BigDecimal;

public class InsufficientFundsException extends RuntimeException {
    public InsufficientFundsException(BigDecimal available, BigDecimal requested) {
        super(String.format("Insufficient funds. Available: %.2f, Requested: %.2f", available, requested));
    }
}
