/* رفتارهای سمت مرورگر — فقط برای راحتی کاربر.
   هر بررسی مهمی سمت سرور هم تکرار می‌شود، چون چیزی که در مرورگر است
   قابل دستکاری است و به آن اعتماد نمی‌کنیم. */
(function () {
  "use strict";

  var FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹";
  var AR_DIGITS = "٠١٢٣٤٥٦٧٨٩";

  function toLatinDigits(text) {
    if (!text) return "";
    return String(text).replace(/[۰-۹]/g, function (d) {
      return FA_DIGITS.indexOf(d);
    }).replace(/[٠-٩]/g, function (d) {
      return AR_DIGITS.indexOf(d);
    });
  }

  function toPersianDigits(text) {
    return String(text).replace(/[0-9]/g, function (d) {
      return FA_DIGITS[+d];
    });
  }

  function groupThousands(value) {
    var text = toLatinDigits(value).replace(/[^\d.\-]/g, "");
    if (!text) return "";
    var negative = text.charAt(0) === "-";
    text = text.replace(/-/g, "");
    var parts = text.split(".");
    var whole = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    var out = parts.length > 1 ? whole + "." + parts[1] : whole;
    return (negative ? "-" : "") + out;
  }

  function parseNumber(value) {
    var text = toLatinDigits(value).replace(/,/g, "").replace(/\s/g, "");
    var n = parseFloat(text);
    return isNaN(n) ? null : n;
  }

  // ---- منوی موبایل ----
  var toggle = document.getElementById("menuToggle");
  var sidebar = document.getElementById("sidebar");
  var backdrop = document.getElementById("sidebarBackdrop");

  function closeMenu() {
    if (sidebar) sidebar.classList.remove("open");
    if (backdrop) backdrop.classList.remove("show");
  }

  if (toggle && sidebar) {
    toggle.addEventListener("click", function () {
      sidebar.classList.toggle("open");
      if (backdrop) backdrop.classList.toggle("show");
    });
  }
  if (backdrop) backdrop.addEventListener("click", closeMenu);

  // ---- بستن پیام‌ها ----
  document.querySelectorAll(".alert-close").forEach(function (btn) {
    btn.addEventListener("click", function () {
      var alert = btn.closest(".alert");
      if (alert) alert.remove();
    });
  });

  // ---- جداکننده هزارگان زنده در فیلدهای مبلغ ----
  document.querySelectorAll(".amount-input").forEach(function (input) {
    input.addEventListener("input", function () {
      var caretFromEnd = input.value.length - input.selectionStart;
      input.value = groupThousands(input.value);
      var pos = Math.max(0, input.value.length - caretFromEnd);
      try { input.setSelectionRange(pos, pos); } catch (e) { /* بی‌اهمیت */ }
      recalcTotal();
    });
    input.addEventListener("blur", function () {
      input.value = groupThousands(input.value);
    });
  });

  // ---- ارقام فارسی در تاریخ ----
  document.querySelectorAll(".date-input").forEach(function (input) {
    input.addEventListener("blur", function () {
      var text = toLatinDigits(input.value).replace(/[.\-]/g, "/").trim();
      var m = text.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/);
      if (m) {
        text = m[1] + "/" + ("0" + m[2]).slice(-2) + "/" + ("0" + m[3]).slice(-2);
      }
      input.value = toPersianDigits(text);
    });
  });

  // ---- محاسبه خودکار مبلغ کل در فرم معامله ----
  var qtyInput = document.getElementById("id_quantity");
  var priceInput = document.getElementById("id_unit_price");
  var totalBox = document.getElementById("totalPreview");

  function recalcTotal() {
    if (!qtyInput || !priceInput || !totalBox) return;
    var qty = parseNumber(qtyInput.value);
    var price = parseNumber(priceInput.value);
    if (qty === null || price === null || qty <= 0 || price <= 0) {
      totalBox.textContent = "—";
      return;
    }
    var total = qty * price;
    totalBox.textContent = toPersianDigits(groupThousands(total.toFixed(0)));
  }

  if (qtyInput && priceInput) {
    [qtyInput, priceInput].forEach(function (el) {
      el.addEventListener("input", recalcTotal);
      el.addEventListener("change", recalcTotal);
    });
    recalcTotal();
  }

  // ---- نمایش مانده طرف حساب کنار فرم ثبت معامله ----
  var partySelect = document.getElementById("id_counterparty") || document.getElementById("id_party");
  var partyPanel = document.getElementById("partyBalance");

  function loadPartyBalance() {
    if (!partySelect || !partyPanel) return;
    var id = partySelect.value;
    if (!id) {
      partyPanel.innerHTML = '<p class="muted">طرف حساب را انتخاب کنید تا مانده فعلی‌اش نشان داده شود.</p>';
      return;
    }
    partyPanel.innerHTML = '<p class="muted">در حال خواندن مانده…</p>';
    fetch("/api/party/" + id + "/balance/", { headers: { "X-Requested-With": "fetch" } })
      .then(function (r) {
        if (!r.ok) throw new Error("failed");
        return r.json();
      })
      .then(function (data) {
        if (!data.rows.length) {
          partyPanel.innerHTML = '<p class="muted">این طرف حساب هیچ مانده‌ای ندارد.</p>';
          return;
        }
        var html = "";
        data.rows.forEach(function (row) {
          var cls = row.negative ? "credit" : "debit";
          html += '<div class="row"><span>' + row.currency + "</span>" +
            '<span class="amount ' + cls + '">' + row.amount +
            " <em>" + row.state + "</em></span></div>";
        });
        html += '<div class="row"><span>ارزش کل به ' + data.unit + "</span>" +
          '<strong class="num">' + data.total_base + "</strong></div>";
        partyPanel.innerHTML = html;
      })
      .catch(function () {
        partyPanel.innerHTML = '<p class="muted">خواندن مانده ممکن نشد.</p>';
      });
  }

  if (partySelect && partyPanel) {
    partySelect.addEventListener("change", loadPartyBalance);
    loadPartyBalance();
  }

  // ---- پیشنهاد شرح: با کلیک، متن در کادر شرح می‌نشیند ----
  var descBox = document.getElementById("id_description");
  var descChips = document.getElementById("descSuggestions");
  if (descBox && descChips) {
    descChips.addEventListener("click", function (event) {
      var chip = event.target.closest(".chip-btn");
      if (!chip) return;
      descBox.value = chip.getAttribute("data-text") || "";
      descBox.focus();
    });
  }

  // ---- موجودی ارزها در نوار بالا، در همه صفحات ----
  var topbarBalances = document.getElementById("topbarBalances");

  function refreshTopbar() {
    if (!topbarBalances || document.hidden) return;
    fetch("/api/topbar-balances/", { headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        if (!data.rows || !data.rows.length) return;
        var html = "";
        data.rows.forEach(function (row) {
          html += '<span class="tb-item"><span class="tb-name">' + row.name +
            '</span><span class="tb-value ' + row.sign + '">' +
            toPersianDigits(row.value) + "</span></span>";
        });
        topbarBalances.innerHTML = html;
      })
      .catch(function () { /* اتصال موقتاً قطع است؛ عددهای قبلی می‌مانند */ });
  }

  if (topbarBalances) {
    setInterval(refreshTopbar, 30000);
  }

  // ---- به‌روزرسانی زنده داشبورد بدون رفرش صفحه ----
  var liveHouse = document.getElementById("liveHouse");
  var liveRates = document.getElementById("liveRates");

  function refreshDashboard() {
    if (!liveHouse && !liveRates) return;
    if (document.hidden) return;
    fetch("/dashboard/data/", { headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
      .then(function (data) {
        if (liveHouse && data.house) {
          var rows = "";
          data.house.forEach(function (item) {
            rows += "<tr><td>" + item.party + "</td><td>" + item.currency +
              '</td><td class="num"><span class="amount ' +
              (item.negative ? "credit" : "debit") + '">' + item.amount + "</span></td></tr>";
          });
          var body = liveHouse.querySelector("tbody");
          if (body && rows) body.innerHTML = rows;
        }
        if (liveRates && data.rates) {
          var rrows = "";
          data.rates.forEach(function (item) {
            rrows += "<tr><td>" + item.currency + '</td><td class="num">' +
              (item.rate || "—") + '</td><td class="muted">' + (item.at || "—") + "</td></tr>";
          });
          var rbody = liveRates.querySelector("tbody");
          if (rbody && rrows) rbody.innerHTML = rrows;
        }
      })
      .catch(function () { /* اتصال موقتاً قطع است؛ دفعه بعد دوباره تلاش می‌شود */ });
  }

  if (liveHouse || liveRates) {
    setInterval(refreshDashboard, 20000);
  }

  // ---- جلوگیری از ارسال دوباره فرم با دابل‌کلیک ----
  document.querySelectorAll("form[data-once]").forEach(function (form) {
    form.addEventListener("submit", function () {
      var btn = form.querySelector('button[type="submit"]');
      if (btn) {
        btn.disabled = true;
        btn.textContent = "در حال ثبت…";
        setTimeout(function () { btn.disabled = false; }, 8000);
      }
    });
  });
})();
