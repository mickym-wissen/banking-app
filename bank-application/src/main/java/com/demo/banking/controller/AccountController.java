package com.demo.banking.controller;

import com.demo.banking.dto.AccountDto;
import com.demo.banking.dto.TransactionDto;
import com.demo.banking.service.AccountService;
import io.micrometer.core.annotation.Timed;
import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.http.*;
import org.springframework.web.bind.annotation.*;

import java.util.List;

@RestController
@RequestMapping("/api/v1/accounts")
@RequiredArgsConstructor
@Slf4j
@Timed(value = "banking.api.requests", extraTags = {"controller", "accounts"})
public class AccountController {

    private final AccountService accountService;

    // ── CRUD ──────────────────────────────────────────────────────────────

    /** POST /api/v1/accounts */
    @PostMapping
    @ResponseStatus(HttpStatus.CREATED)
    public AccountDto.Response createAccount(@Valid @RequestBody AccountDto.CreateRequest request) {
        log.info("POST /accounts - create for owner={}", request.getOwnerName());
        return accountService.createAccount(request);
    }

    /** GET /api/v1/accounts */
    @GetMapping
    public List<AccountDto.Response> getAllAccounts() {
        log.info("GET /accounts - list all");
        return accountService.getAllAccounts();
    }

    /** GET /api/v1/accounts/{id} */
    @GetMapping("/{id}")
    public AccountDto.Response getAccountById(@PathVariable Long id) {
        log.info("GET /accounts/{}", id);
        return accountService.getAccountById(id);
    }

    /** GET /api/v1/accounts/number/{accountNumber} */
    @GetMapping("/number/{accountNumber}")
    public AccountDto.Response getAccountByNumber(@PathVariable String accountNumber) {
        log.info("GET /accounts/number/{}", accountNumber);
        return accountService.getAccountByNumber(accountNumber);
    }

    /** PUT /api/v1/accounts/{id} */
    @PutMapping("/{id}")
    public AccountDto.Response updateAccount(
            @PathVariable Long id,
            @RequestBody AccountDto.UpdateRequest request) {
        log.info("PUT /accounts/{}", id);
        return accountService.updateAccount(id, request);
    }

    /** DELETE /api/v1/accounts/{id} */
    @DeleteMapping("/{id}")
    @ResponseStatus(HttpStatus.NO_CONTENT)
    public void deleteAccount(@PathVariable Long id) {
        log.info("DELETE /accounts/{}", id);
        accountService.deleteAccount(id);
    }

    // ── Transactions ──────────────────────────────────────────────────────

    /** POST /api/v1/accounts/{id}/deposit */
    @PostMapping("/{id}/deposit")
    public TransactionDto.Response deposit(
            @PathVariable Long id,
            @Valid @RequestBody TransactionDto.MoneyRequest request) {
        log.info("POST /accounts/{}/deposit amount={}", id, request.getAmount());
        return accountService.deposit(id, request);
    }

    /** POST /api/v1/accounts/{id}/withdraw */
    @PostMapping("/{id}/withdraw")
    public TransactionDto.Response withdraw(
            @PathVariable Long id,
            @Valid @RequestBody TransactionDto.MoneyRequest request) {
        log.info("POST /accounts/{}/withdraw amount={}", id, request.getAmount());
        return accountService.withdraw(id, request);
    }

    /** POST /api/v1/accounts/{id}/transfer */
    @PostMapping("/{id}/transfer")
    public List<TransactionDto.Response> transfer(
            @PathVariable Long id,
            @Valid @RequestBody TransactionDto.TransferRequest request) {
        log.info("POST /accounts/{}/transfer to={} amount={}", id, request.getToAccountId(), request.getAmount());
        return accountService.transfer(id, request);
    }

    /** GET /api/v1/accounts/{id}/transactions */
    @GetMapping("/{id}/transactions")
    public List<TransactionDto.Response> getTransactionHistory(@PathVariable Long id) {
        log.info("GET /accounts/{}/transactions", id);
        return accountService.getTransactionHistory(id);
    }
}
