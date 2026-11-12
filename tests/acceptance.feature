Feature: Investigate merchant settlement performance
  Scenario: Unsettled successful payment
    Given a successful payment with no completed settlement
    When the pipeline processes it
    Then the payment appears as UNSETTLED with the full amount outstanding

  Scenario: Split settlements
    Given a payment for 10000 with completed settlements for 8000 and 2000
    When reporting totals are calculated
    Then payment volume and settled value are both 10000 and the gap is zero

  Scenario: Settlement SLA boundary
    Given a payment becomes fully settled exactly 30 minutes after payment
    When the SLA is calculated
    Then the completion meets the SLA

  Scenario: Historical merchant risk
    Given a merchant was LOW in February and HIGH in April
    When reporting a February payment
    Then the payment uses LOW risk

  Scenario: Late and out-of-order events
    Given an earlier lifecycle event arrives after a later one
    When the batch is processed
    Then business order uses event time and arrival lag uses ingestion time

  Scenario: Incremental replay
    Given a batch adds a settlement for an existing transaction
    When the same batch is loaded twice
    Then the original payment's totals change once

  Scenario: Invalid and unmatched records
    Given a negative settlement and a settlement with no matching transaction
    When the batch runs
    Then both records remain traceable with their respective dispositions

  Scenario: Merchant thresholds
    Given a merchant has a 94 percent settlement rate and 92 percent SLA
    When merchant performance is reported
    Then it appears in exceptions and is excluded from the Merchant Risk count
