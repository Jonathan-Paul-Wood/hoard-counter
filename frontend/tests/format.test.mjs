import test from "node:test";
import assert from "node:assert/strict";
import {
  money,
  decimalSum,
  decimalSubtract,
  percentageToRate,
  setCurrencyPrecisions,
  query,
} from "../lib/api.ts";

test("money preserves values larger than the safe floating point integer", () => {
  assert.equal(money("9007199254740993.01"), "$9,007,199,254,740,993.01");
});
test("ETH and BTC retain native precision without binary rounding", () => {
  assert.equal(
    money("0.123456789123456789", "ETH"),
    "0.123456789123456789 ETH",
  );
  assert.equal(money("0.00000001", "BTC"), "0.00000001 BTC");
});
test("display rounds exact decimal strings, handles debt and missing values", () => {
  assert.equal(money("0.995"), "$1.00");
  assert.equal(money("0.985"), "$0.98");
  assert.equal(money("-1234.20"), "$−1,234.20");
  assert.equal(money(null), "—");
  assert.equal(money(undefined), "—");
  assert.equal(money("0"), "$0.00");
});
test("custom currency precision applies without changing fiat defaults", () => {
  setCurrencyPrecisions([{ code: "XYZ", precision: 4 }]);
  assert.equal(money("1.2345", "XYZ"), "XYZ1.2345");
  assert.equal(money("123.6", "JPY"), "¥124");
});
test("decimal totals remain exact and report filters omit blank values", () => {
  assert.equal(
    decimalSum(["9007199254740993.01", "0.99", "-1"]),
    "9007199254740993.00",
  );
  assert.equal(
    query({
      start: "2026-01-01",
      account_id: "",
      month: null,
      type: "Expense",
    }),
    "start=2026-01-01&type=Expense",
  );
});
test("refund balances and percentage assumptions use decimal arithmetic", () => {
  assert.equal(decimalSubtract("100.00", "-25.35"), "125.35");
  assert.equal(percentageToRate("0.07"), "0.0007");
  assert.equal(percentageToRate("7.25"), "0.0725");
});
