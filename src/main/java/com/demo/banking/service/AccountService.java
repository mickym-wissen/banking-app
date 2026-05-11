package com.demo.banking.service;

import com.demo.banking.dto.AccountDto;
import com.demo.banking.dto.TransactionDto;
import com.demo.banking.exception.AccountNotFoundException;
import com.demo.banking.exception.InsufficientFundsException;
import com.demo.banking.model.Account;
import com.demo.banking.model.Transaction;
import com.demo.banking.repository.AccountRepository;
import com.demo.banking.repository.TransactionRepository;
import io.micrometer.core.instrument.*;
import jakarta.annotation.PostConstruct;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.List;
import java.util.concurrent.atomic.AtomicLong;

@Service
@RequiredArgsConstructor
@Slf4j
public class AccountService {

    private final AccountRepository accountRepository;
    private final TransactionRepository transactionRepository;
    private final MeterRegistry meterRegistry;    // injected by Micrometer / Datadog registry

    // ── Custom Datadog metrics ────────────────────────────────────────────
    private Counter accountCreatedCounter;
    private Counter accountDeletedCounter;
    private Counter depositCounter;
    private Counter withdrawalCounter;
    private Counter transferCounter;
    private Timer   transactionTimer;
    private AtomicLong totalActiveAccounts;

    @PostConstruct
    void initMetrics() {
        accountCreatedCounter = Counter.builder("banking.accounts.created")
                .description("Total accounts created")
                .register(meterRegistry);

        accountDeletedCounter = Counter.builder("banking.accounts.deleted")
                .description("Total accounts deleted")
                .register(meterRegistry);

        depositCounter = Counter.builder("banking.transactions.deposits")
                .description("Total deposit transactions")
                .register(meterRegistry);

        withdrawalCounter = Counter.builder("banking.transactions.withdrawals")
                .description("Total withdrawal transactions")
                .register(meterRegistry);

        transferCounter = Counter.builder("banking.transactions.transfers")
                .description("Total transfer transactions")
                .register(meterRegistry);

        transactionTimer = Timer.builder("banking.transaction.duration")
                .description("Time taken to process a transaction")
                .register(meterRegistry);

        totalActiveAccounts = meterRegistry.gauge(
                "banking.accounts.active_total",
                new AtomicLong(0));
    }

    // ────────────────────────────────────────────────────────────────────
    //  CREATE
    // ────────────────────────────────────────────────────────────────────
    @Transactional
    public AccountDto.Response createAccount(AccountDto.CreateRequest req) {
        log.info("Creating account for owner: {}", req.getOwnerName());

        Account account = Account.builder()
                .ownerName(req.getOwnerName())
                .accountNumber(generateAccountNumber())
                .accountType(req.getAccountType())
                .balance(req.getInitialBalance())
                .build();

        Account saved = accountRepository.save(account);

        // Record initial deposit as a transaction if balance > 0
        if (req.getInitialBalance().compareTo(BigDecimal.ZERO) > 0) {
            recordTransaction(saved, Transaction.TransactionType.CREDIT,
                    req.getInitialBalance(), saved.getBalance(), "Initial deposit");
        }

        accountCreatedCounter.increment();
        if (totalActiveAccounts != null) totalActiveAccounts.incrementAndGet();

        log.info("Account created: id={}, accountNumber={}", saved.getId(), saved.getAccountNumber());
        return AccountDto.Response.from(saved);
    }

    // ────────────────────────────────────────────────────────────────────
    //  READ
    // ────────────────────────────────────────────────────────────────────
    @Transactional(readOnly = true)
    public List<AccountDto.Response> getAllAccounts() {
        log.debug("Fetching all accounts");
        return accountRepository.findAll()
                .stream()
                .map(AccountDto.Response::from)
                .toList();
    }

    @Transactional(readOnly = true)
    public AccountDto.Response getAccountById(Long id) {
        log.debug("Fetching account id={}", id);
        return AccountDto.Response.from(findAccountById(id));
    }

    @Transactional(readOnly = true)
    public AccountDto.Response getAccountByNumber(String accountNumber) {
        log.debug("Fetching account number={}", accountNumber);
        return AccountDto.Response.from(
                accountRepository.findByAccountNumber(accountNumber)
                        .orElseThrow(() -> new AccountNotFoundException(accountNumber))
        );
    }

    // ────────────────────────────────────────────────────────────────────
    //  UPDATE
    // ────────────────────────────────────────────────────────────────────
    @Transactional
    public AccountDto.Response updateAccount(Long id, AccountDto.UpdateRequest req) {
        log.info("Updating account id={}", id);
        Account account = findAccountById(id);

        if (req.getOwnerName() != null && !req.getOwnerName().isBlank()) {
            account.setOwnerName(req.getOwnerName());
        }
        if (req.getStatus() != null) {
            account.setStatus(req.getStatus());
            if (req.getStatus() == Account.AccountStatus.INACTIVE && totalActiveAccounts != null) {
                totalActiveAccounts.decrementAndGet();
            }
        }

        Account updated = accountRepository.save(account);
        log.info("Account updated: id={}", id);
        return AccountDto.Response.from(updated);
    }

    // ────────────────────────────────────────────────────────────────────
    //  DELETE
    // ────────────────────────────────────────────────────────────────────
    @Transactional
    public void deleteAccount(Long id) {
        log.info("Deleting account id={}", id);
        Account account = findAccountById(id);
        accountRepository.delete(account);
        accountDeletedCounter.increment();
        if (totalActiveAccounts != null) totalActiveAccounts.decrementAndGet();
        log.info("Account deleted: id={}", id);
    }

    // ────────────────────────────────────────────────────────────────────
    //  TRANSACTIONS
    // ────────────────────────────────────────────────────────────────────
    @Transactional
    public TransactionDto.Response deposit(Long accountId, TransactionDto.MoneyRequest req) {
        return transactionTimer.record(() -> {
            log.info("Deposit accountId={} amount={}", accountId, req.getAmount());
            Account account = findActiveAccount(accountId);
            account.setBalance(account.getBalance().add(req.getAmount()));
            accountRepository.save(account);

            Transaction tx = recordTransaction(account, Transaction.TransactionType.CREDIT,
                    req.getAmount(), account.getBalance(), req.getDescription());

            depositCounter.increment();
            meterRegistry.counter("banking.transactions.amount",
                    "type", "deposit").increment(req.getAmount().doubleValue());

            log.info("Deposit complete accountId={} newBalance={}", accountId, account.getBalance());
            return TransactionDto.Response.from(tx);
        });
    }

    @Transactional
    public TransactionDto.Response withdraw(Long accountId, TransactionDto.MoneyRequest req) {
        return transactionTimer.record(() -> {
            log.info("Withdrawal accountId={} amount={}", accountId, req.getAmount());
            Account account = findActiveAccount(accountId);

            if (account.getBalance().compareTo(req.getAmount()) < 0) {
                throw new InsufficientFundsException(account.getBalance(), req.getAmount());
            }

            account.setBalance(account.getBalance().subtract(req.getAmount()));
            accountRepository.save(account);

            Transaction tx = recordTransaction(account, Transaction.TransactionType.DEBIT,
                    req.getAmount(), account.getBalance(), req.getDescription());

            withdrawalCounter.increment();
            meterRegistry.counter("banking.transactions.amount",
                    "type", "withdrawal").increment(req.getAmount().doubleValue());

            log.info("Withdrawal complete accountId={} newBalance={}", accountId, account.getBalance());
            return TransactionDto.Response.from(tx);
        });
    }

    @Transactional
    public List<TransactionDto.Response> transfer(Long fromAccountId, TransactionDto.TransferRequest req) {
        return transactionTimer.record(() -> {
            log.info("Transfer from={} to={} amount={}", fromAccountId, req.getToAccountId(), req.getAmount());
            Account from = findActiveAccount(fromAccountId);
            Account to   = findActiveAccount(req.getToAccountId());

            if (from.getBalance().compareTo(req.getAmount()) < 0) {
                throw new InsufficientFundsException(from.getBalance(), req.getAmount());
            }

            from.setBalance(from.getBalance().subtract(req.getAmount()));
            to.setBalance(to.getBalance().add(req.getAmount()));
            accountRepository.save(from);
            accountRepository.save(to);

            String desc = req.getDescription() != null ? req.getDescription() : "Transfer";
            Transaction debitTx  = recordTransaction(from, Transaction.TransactionType.TRANSFER,
                    req.getAmount(), from.getBalance(), desc + " -> Acc#" + to.getAccountNumber());
            Transaction creditTx = recordTransaction(to,   Transaction.TransactionType.TRANSFER,
                    req.getAmount(), to.getBalance(),   desc + " <- Acc#" + from.getAccountNumber());

            transferCounter.increment();
            log.info("Transfer complete from={} to={}", fromAccountId, req.getToAccountId());
            return List.of(TransactionDto.Response.from(debitTx), TransactionDto.Response.from(creditTx));
        });
    }

    @Transactional(readOnly = true)
    public List<TransactionDto.Response> getTransactionHistory(Long accountId) {
        findAccountById(accountId); // verify exists
        return transactionRepository.findByAccountIdOrderByCreatedAtDesc(accountId)
                .stream()
                .map(TransactionDto.Response::from)
                .toList();
    }

    // ────────────────────────────────────────────────────────────────────
    //  Helpers
    // ────────────────────────────────────────────────────────────────────
    private Account findAccountById(Long id) {
        return accountRepository.findById(id)
                .orElseThrow(() -> new AccountNotFoundException(id));
    }

    private Account findActiveAccount(Long id) {
        Account account = findAccountById(id);
        if (account.getStatus() != Account.AccountStatus.ACTIVE) {
            throw new IllegalStateException("Account id=" + id + " is not active (status=" + account.getStatus() + ")");
        }
        return account;
    }

    private Transaction recordTransaction(Account account, Transaction.TransactionType type,
                                          BigDecimal amount, BigDecimal balanceAfter, String description) {
        Transaction tx = Transaction.builder()
                .account(account)
                .type(type)
                .amount(amount)
                .balanceAfter(balanceAfter)
                .description(description)
                .build();
        return transactionRepository.save(tx);
    }

    private String generateAccountNumber() {
        String base = "ACC" + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd"))
                + (long) (Math.random() * 1_000_000);
        // ensure uniqueness
        while (accountRepository.existsByAccountNumber(base)) {
            base = base + "X";
        }
        return base;
    }
}
