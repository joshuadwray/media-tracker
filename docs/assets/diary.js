/* media-tracker client renderer — HAND-WRITTEN, the generators never
   clobber this file (same deal as docs/reading/log.html).

   Why this exists: the diary and lists are human-authored surfaces. You
   type something and immediately want to see it, but a static build put a
   CI run plus a GitHub Pages deploy (~56s, measured) between the keystroke
   and the pixels — and Pages' `cache-control: max-age=600` could hide the
   result for ten more minutes. So the pages stopped being documents that
   CI renders and became shells that render themselves from data.

   The split: CI does only what a browser CANNOT — the iTunes / OpenLibrary
   / Apple Books lookups behind covers and page counts — and publishes
   docs/data/{diary,lists}.json. Everything freshness-sensitive (per-day
   deltas, streaks, the calendar) is computed here, because it has to fold
   in edits saved seconds ago that no build has seen yet.

   Freshness model:
     1. paint from the localStorage snapshot        (instant, no network)
     2. overlay mt_pending — the exact log last PUT (instant, no network)
     3. fetch build.json no-store; if a bundle hash moved, refetch + repaint
     4. drop the overlay once a build contains it   (self-healing)
   Steps 1-2 are why a save looks instant; step 4 is why it can't drift. */
(function () {
  'use strict';

  var REPO = 'joshuadwray/media-tracker';
  var SNAP = 'mt_snapshot';   // {stamp, diary, lists}
  var PEND = 'mt_pending';    // {savedAt, log} — the whole log, as PUT
  var PENDL = 'mt_pending_lists';  // {stem: {savedAt, items}} — as PUT
  var PEND_TTL_MS = 7 * 24 * 3600 * 1000;
  var DOWS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
                'July', 'August', 'September', 'October', 'November',
                'December'];

  // ------------------------------------------------------------ helpers

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#x27;');
  }
  // Python's %g for the ratings: 4.0 -> "4", 3.5 -> "3.5"
  function g(n) { return String(+n); }
  function lsGet(k) {
    try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; }
  }
  function lsSet(k, v) {
    try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* full/private */ }
  }
  function lsDel(k) { try { localStorage.removeItem(k); } catch (e) { } }

  // Dates stay ISO strings end to end; Date is only ever built from
  // explicit Y/M/D in UTC, never parsed from a string (new Date('2026-01-01')
  // is UTC-midnight and silently shifts a day in a negative offset like ours).
  function todayISO() {
    var d = new Date();
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function addDays(iso, n) {
    var p = iso.split('-');
    var d = new Date(Date.UTC(+p[0], +p[1] - 1, +p[2] + n));
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-'
      + pad(d.getUTCDate());
  }
  function ym(iso) { return iso.slice(0, 7); }
  function tileHue(title) {
    var sum = 0;
    for (var ch of String(title)) sum += ch.codePointAt(0);
    return sum % 360;
  }
  function cacheKey(title, author) {
    return String(title || '').trim().toLowerCase() + '|'
      + String(author || '').trim().toLowerCase();
  }

  // ------------------------------------------------- page math (ported)

  // Mirrors reading_gen.daily_pages: cumulative pages -> per-day deltas, a
  // backwards session counts as a correction (delta 0), and a finished book
  // credits its unlogged remainder to the finish date.
  function dailyPages(b) {
    var out = {}, prev = 0;
    for (var i = 0; i < b.sessions.length; i++) {
      var day = b.sessions[i][0], page = b.sessions[i][1];
      var delta = page - prev;
      if (delta < 0) delta = 0;
      out[day] = (out[day] || 0) + delta;
      prev = Math.max(prev, page);
    }
    if (b.status === 'finished' && b.finished && b.pageCount
        && prev < b.pageCount)
      out[b.finished] = (out[b.finished] || 0) + (b.pageCount - prev);
    return out;
  }

  function pagesByDate(books) {
    var totals = {}, readers = {};
    for (var i = 0; i < books.length; i++) {
      var b = books[i], dp = dailyPages(b);
      for (var day in dp) {
        totals[day] = (totals[day] || 0) + dp[day];
        if (dp[day] > 0) {
          if (!readers[day]) readers[day] = [];
          if (readers[day].indexOf(b) < 0) readers[day].push(b);
        }
      }
    }
    return { totals: totals, readers: readers };
  }

  function streak(totals, today) {
    var day = (totals[today] || 0) > 0 ? today : addDays(today, -1), n = 0;
    while ((totals[day] || 0) > 0) { n++; day = addDays(day, -1); }
    return n;
  }

  // reading_gen.group_reads: re-reads are separate entries sharing
  // title|author; the first in file order owns the shared page slug.
  function regroup(books) {
    var groups = {}, order = [];
    for (var i = 0; i < books.length; i++) {
      var k = books[i].key;
      if (!groups[k]) { groups[k] = []; order.push(k); }
      groups[k].push(books[i]);
    }
    for (var j = 0; j < order.length; j++) {
      var reads = groups[order[j]];
      for (var r = 0; r < reads.length; r++) reads[r].base = reads[0].slug;
    }
    return { groups: groups, order: order };
  }

  function stars(r) {
    var full = Math.floor(r), half = (r - full) >= 0.5, out = '';
    for (var i = 0; i < full; i++) out += '★';
    if (half) out += "<span class='half'>★</span>";
    for (var k = 0; k < 5 - full - (half ? 1 : 0); k++) out += '☆';
    return "<span class='stars' title='" + g(r) + "/5'>" + out + '</span>';
  }

  function chip(rating) {
    if (rating == null) return '';
    var whole = Math.floor(rating), half = rating - whole ? '½' : '';
    return "<span class='fchip' title='" + g(rating) + "/5'>★"
      + whole + half + '</span>';
  }

  // ------------------------------------------------- pending (the overlay)

  // The whole log exactly as it was PUT. Correct by construction: it IS the
  // truth the server now holds, so the merge never has to guess at a diff.
  function savePending(log) {
    lsSet(PEND, { savedAt: Date.now(), log: log });
    var snap = lsGet(SNAP);
    if (snap) { try { paint(snap); } catch (e) { /* editor still succeeded */ } }
    polls = 0;
    poll();   // start watching for the build that makes this permanent
  }
  window.mtSavePending = savePending;

  function sessionPairs(raw) {
    var out = [];
    for (var i = 0; i < (raw || []).length; i++) {
      var m = /^(\d{4}-\d{2}-\d{2}) (\d+)$/.exec(String(raw[i]));
      if (m) out.push([m[1], +m[2]]);
    }
    return out;
  }

  function sameBook(pb, e) {
    if (!e) return false;
    var ps = sessionPairs(pb.sessions);
    if (ps.length !== e.sessions.length) return false;
    for (var i = 0; i < ps.length; i++)
      if (ps[i][0] !== e.sessions[i][0] || ps[i][1] !== e.sessions[i][1])
        return false;
    return (pb.title || '') === e.title
      && (pb.author || '') === e.author
      && (pb.status || 'reading') === e.status
      && (pb.rating == null ? null : +pb.rating) === (e.rating == null ? null : +e.rating)
      && (pb.finished || null) === (e.finished || null)
      && (pb.started || null) === (e.started || null);
  }

  // Drop the overlay once the build caught up (or it went stale), so a
  // failed/superseded save can't shadow the real data forever.
  function reconcile(diary) {
    var pend = lsGet(PEND);
    if (!pend || !pend.log) return null;
    if (Date.now() - (pend.savedAt || 0) > PEND_TTL_MS) { lsDel(PEND); return null; }
    var by = {}, i;
    for (i = 0; i < diary.books.length; i++) by[diary.books[i].slug] = diary.books[i];
    var books = pend.log.books || [];
    if (books.length === diary.books.length) {
      var all = true;
      for (i = 0; i < books.length; i++)
        if (!sameBook(books[i], by[books[i].slug])) { all = false; break; }
      if (all) { lsDel(PEND); return null; }
    }
    return pend;
  }

  // Pending supplies the facts; the bundle supplies the enrichment CI alone
  // can fetch (cover, page count). A book saved seconds ago has no cover
  // yet and falls back to a typographic tile — exactly what the old static
  // build did until the next run.
  function mergeBooks(diary, pend) {
    if (!pend) return diary.books;
    var by = {}, i;
    for (i = 0; i < diary.books.length; i++) by[diary.books[i].slug] = diary.books[i];
    var out = [];
    var raw = pend.log.books || [];
    for (i = 0; i < raw.length; i++) {
      var pb = raw[i], e = by[pb.slug] || null;
      out.push({
        slug: pb.slug,
        base: pb.slug,
        key: cacheKey(pb.title, pb.author),
        title: pb.title || '',
        author: pb.author || '',
        status: pb.status || 'reading',
        rating: pb.rating == null ? null : +pb.rating,
        started: pb.started || null,
        finished: pb.finished || null,
        pageCount: pb.page_count != null ? +pb.page_count
          : (e ? e.pageCount : null),
        pageSource: pb.page_count != null ? 'manual' : (e ? e.pageSource : null),
        cover: e ? e.cover : null,
        hue: e ? e.hue : tileHue(pb.title || ''),
        sessions: sessionPairs(pb.sessions),
        syncing: !sameBook(pb, e)
      });
    }
    return out;
  }

  // ------------------------------------------------------------ data I/O

  function dataUrl(name, bust) {
    var depth = document.body.getAttribute('data-depth') || '1';
    return new Array(+depth + 1).join('../') + 'data/' + name
      + (bust ? '?v=' + bust : '');
  }

  function getJSON(url, opts) {
    return fetch(url, opts || {}).then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    });
  }

  // build.json is the only unconditional request, and it is deliberately
  // no-store: it is the thing that has to defeat the 10-minute page cache.
  // The bundles are fetched with their content hash in the query string, so
  // they cache hard and are only refetched when they actually changed.
  function refresh(snap) {
    return getJSON(dataUrl('build.json'), { cache: 'no-store' })
      .then(function (stamp) {
        var have = (snap && snap.stamp) || {};
        var jobs = [], next = { stamp: stamp, diary: snap && snap.diary,
                                lists: snap && snap.lists };
        if (!next.diary || have.diary !== stamp.diary)
          jobs.push(getJSON(dataUrl('diary.json', stamp.diary))
            .then(function (d) { next.diary = d; }));
        if (!next.lists || have.lists !== stamp.lists)
          jobs.push(getJSON(dataUrl('lists.json', stamp.lists))
            .then(function (d) { next.lists = d; }));
        if (!jobs.length) return null;
        return Promise.all(jobs).then(function () { return next; });
      });
  }

  // ------------------------------------------------------- status pill

  function setStatus(state, text) {
    var el = document.getElementById('mt-fresh');
    if (!el) {
      el = document.createElement('div');
      el.id = 'mt-fresh';
      document.body.appendChild(el);
    }
    el.className = 'mt-fresh ' + state;
    el.textContent = text;
    if (state === 'ok') setTimeout(function () {
      if (el.className.indexOf('ok') >= 0) el.className = 'mt-fresh gone';
    }, 2500);
  }

  // ------------------------------------------------------------ renderers

  // The ★ chip belongs to the day the book was FINISHED, not to every day
  // it was read — otherwise a long book stamps its rating across weeks.
  function bookThumb(b, href, day) {
    var th = b.cover
      ? "<img src='" + esc(b.cover) + "' alt='' loading='lazy'>"
      : "<div class='dot'></div>";
    var c = (b.status === 'finished' && b.finished === day && b.rating != null)
      ? chip(b.rating) : '';
    return "<a class='th' href='" + esc(href) + "'>" + th + c + '</a>';
  }

  function renderCalendar(d) {
    var books = d.books, films = d.films, today = todayISO();
    var pd = pagesByDate(books), totals = pd.totals, readers = pd.readers;
    var goal = d.dailyGoal || 0;
    var filmsByDay = {};
    films.forEach(function (f) {
      (filmsByDay[f.watched] = filmsByDay[f.watched] || []).push(f);
    });

    var out = [];
    out.push("<a class='back' href='log.html'>log a session</a>");
    out.push('<h1>Diary</h1>');
    out.push("<div class='vt'><strong>calendar</strong> &middot; "
      + "<a href='list.html'>list</a></div>");

    var reading = books.filter(function (b) { return b.status === 'reading'; });
    if (reading.length) out.push('<h2>Currently reading</h2>');
    reading.forEach(function (b) {
      var at = 0;
      b.sessions.forEach(function (s) { at = Math.max(at, s[1]); });
      var pct = b.pageCount ? Math.min(100, Math.round(at * 100 / b.pageCount)) : 0;
      var img = b.cover ? "<img src='" + esc(b.cover) + "' alt='' loading='lazy'>"
        : "<div class='noimg'></div>";
      var prog = b.pageCount ? 'p.' + at + ' / ' + b.pageCount + ' &middot; ' + pct + '%'
        : 'p.' + at;
      out.push("<a class='cur" + (b.syncing ? ' syncing' : '')
        + "' style='text-decoration:none;color:inherit' href='"
        + esc(b.base) + ".html'>" + img + "<div class='info'>"
        + "<div class='t'>" + esc(b.title) + '</div>'
        + "<div class='meta'>" + esc(b.author) + ' &middot; ' + prog + '</div>'
        + "<div class='bar'><div style='width:" + pct + "%'></div></div>"
        + '</div></a>');
    });

    var weekStart = addDays(today, -6), week = 0;
    for (var dkey in totals)
      if (dkey >= weekStart && dkey <= today) week += totals[dkey];
    out.push("<div class='stats'>"
      + "<div class='stat'><div class='n'>" + streak(totals, today)
      + "</div><div class='l'>day streak</div></div>"
      + "<div class='stat'><div class='n'>" + (totals[today] || 0)
      + "</div><div class='l'>pages today (goal " + goal + ')</div></div>'
      + "<div class='stat'><div class='n'>" + week
      + "</div><div class='l'>this week (goal " + (goal * 7) + ')</div></div>'
      + '</div>');

    var monthSet = {};
    for (var t in totals) monthSet[ym(t)] = 1;
    for (var f in filmsByDay) monthSet[ym(f)] = 1;
    var months = Object.keys(monthSet).sort().reverse();

    if (months.length) {
      var opts = months.map(function (m, i) {
        return "<option value='" + i + "'>" + MONTHS[+m.slice(5, 7) - 1]
          + ' ' + m.slice(0, 4) + '</option>';
      }).join('');
      out.push("<div class='mnav'><button id='mold'>&larr; older</button>"
        + "<select id='mjump' hidden>" + opts + '</select>'
        + "<button id='mnew'>newer &rarr;</button></div>");
    }

    months.forEach(function (mk) {
      var y = +mk.slice(0, 4), mo = +mk.slice(5, 7);
      out.push("<div class='month'><h3>" + MONTHS[mo - 1] + ' ' + y
        + "</h3><div class='cal'>");
      DOWS.forEach(function (n) { out.push("<div class='dow'>" + n + '</div>'); });
      // Sunday-first grid over whole weeks, matching calendar.Calendar(6)
      var first = new Date(Date.UTC(y, mo - 1, 1));
      var lead = first.getUTCDay();
      var days = new Date(Date.UTC(y, mo, 0)).getUTCDate();
      var last = new Date(Date.UTC(y, mo - 1, days)).getUTCDay();
      var cell;
      for (cell = 0; cell < lead; cell++) out.push("<div class='day blank'></div>");
      for (var dayn = 1; dayn <= days; dayn++) {
        var iso = y + '-' + pad(mo) + '-' + pad(dayn);
        var pages = totals[iso] || 0, dayFilms = filmsByDay[iso] || [];
        var cls = (goal && pages >= goal) ? 'day goal' : 'day';
        var thumbs = '';
        if (pages > 0 || dayFilms.length) {
          var tt = [];
          (readers[iso] || []).forEach(function (b) {
            tt.push(bookThumb(b, b.base + '.html', iso));
          });
          dayFilms.forEach(function (f) {
            var th = f.poster
              ? "<img src='" + esc(f.poster) + "' alt='' loading='lazy'>"
              : "<div class='dot'></div>";
            tt.push("<a class='th film' href='../watching/" + esc(f.slug)
              + ".html'>" + th + chip(f.rating) + '</a>');
          });
          if (tt.length > 3)
            tt = tt.slice(0, 2).concat(["<span class='more'>+"
              + (tt.length - 2) + '</span>']);
          thumbs = "<div class='thumbs'>" + tt.join('') + '</div>';
        }
        out.push("<div class='" + cls + "'><span class='dn'>" + dayn
          + '</span>' + thumbs + '</div>');
      }
      for (cell = last; cell < 6; cell++) out.push("<div class='day blank'></div>");
      out.push('</div></div>');
    });

    if (!months.length)
      out.push("<div class='meta'>no sessions logged yet &mdash; "
        + "<a href='log.html'>log one</a></div>");
    return { html: out.join(''), months: months.length };
  }

  function wireMonthNav() {
    var ms = [].slice.call(document.querySelectorAll('.month'));
    if (!ms.length) return;
    var i = 0, o = document.getElementById('mold'),
      n = document.getElementById('mnew'), j = document.getElementById('mjump');
    if (!o || !n || !j) return;
    j.hidden = false;
    function show() {
      ms.forEach(function (m, k) { m.style.display = k === i ? '' : 'none'; });
      o.disabled = i >= ms.length - 1; n.disabled = i <= 0; j.value = i;
    }
    o.onclick = function () { if (i < ms.length - 1) { i++; show(); } };
    n.onclick = function () { if (i > 0) { i--; show(); } };
    j.onchange = function () { i = +j.value; show(); };
    show();
  }

  function renderFlatList(d) {
    var today = todayISO(), rows = {};
    d.books.forEach(function (b) {
      var perDay = {}, sessPage = {}, prev = 0;
      b.sessions.forEach(function (s) {
        var day = s[0], page = s[1];
        var delta = Math.max(0, page - prev);
        prev = Math.max(prev, page);
        var cur = perDay[day] || [0, 0];
        perDay[day] = [cur[0] + delta, Math.max(cur[1], page)];
        sessPage[day] = page;
      });
      if (b.status === 'finished' && b.finished && b.pageCount && prev < b.pageCount) {
        var c = perDay[b.finished] || [0, 0];
        perDay[b.finished] = [c[0] + (b.pageCount - prev), b.pageCount];
      }
      var th = b.cover ? "<img src='" + esc(b.cover) + "' alt='' loading='lazy'>"
        : "<div class='dot'></div>";
      for (var day in perDay) {
        var delta = perDay[day][0], at = perDay[day][1];
        if (delta <= 0) continue;   // corrections read no pages
        var prog = b.pageCount ? 'p.' + at + ' / ' + b.pageCount : 'p.' + at;
        var right = [prog + ' <b>+' + delta + '</b>'];
        if (b.status === 'finished' && b.finished === day) {
          right.push('finished');
          if (b.rating != null) right.push(stars(b.rating));
        } else if (b.status === 'abandoned' && b.finished === day) {
          right.push('abandoned');
        }
        var by = b.author ? " <span class='by'>&mdash; " + esc(b.author) + '</span>' : '';
        var row = "<a class='row" + (b.syncing ? ' syncing' : '')
          + "' style='text-decoration:none;color:inherit' href='"
          + esc(b.base) + ".html'>" + th
          + "<div class='rt'>" + esc(b.title) + by + '</div>'
          + "<div class='rm'>" + right.join(' &middot; ') + '</div></a>';
        // edit only where a real session line exists — the finish-remainder
        // row is synthetic, there is nothing in log.json to point at
        var btn = sessPage[day] != null
          ? "<button class='rowedit' title='edit session' data-slug='"
            + esc(b.slug) + "' data-date='" + day + "' data-page='"
            + sessPage[day] + "'>✎</button>"
          : '';
        (rows[day] = rows[day] || []).push(
          "<div class='rowwrap'>" + row + btn + '</div>');
      }
    });

    d.films.forEach(function (f) {
      var th = f.poster ? "<img src='" + esc(f.poster) + "' alt='' loading='lazy'>"
        : "<div class='dot'></div>";
      var heading = f.year ? f.title + ' (' + f.year + ')' : f.title;
      var right = [];
      if (f.rating != null) right.push(stars(f.rating));
      if (f.rewatch) right.push('↻');
      if (f.liked) right.push("<span class='heart'>♥</span>");
      (rows[f.watched] = rows[f.watched] || []).push(
        "<a class='row film' style='text-decoration:none;color:inherit' href='"
        + '../watching/' + esc(f.slug) + ".html'>" + th
        + "<div class='rt'>" + esc(heading) + '</div>'
        + "<div class='rm'>" + right.join(' &middot; ') + '</div></a>');
    });

    var out = [];
    out.push("<a class='back' href='log.html'>log a session</a>");
    out.push('<h1>Diary</h1>');
    out.push("<div class='vt'><a href='index.html'>calendar</a> &middot; "
      + '<strong>list</strong></div>');
    out.push("<div class='dl'>");
    var days = Object.keys(rows).sort().reverse();
    days.forEach(function (day) {
      var label = MONTHS[+day.slice(5, 7) - 1] + ' ' + (+day.slice(8, 10));
      if (day.slice(0, 4) !== today.slice(0, 4)) label += ', ' + day.slice(0, 4);
      out.push('<h3>' + label + '</h3>');
      out.push(rows[day].join(''));
    });
    if (!days.length)
      out.push("<div class='meta'>no sessions logged yet &mdash; "
        + "<a href='log.html'>log one</a></div>");
    out.push('</div>');
    return out.join('');
  }

  function statusLine(b) {
    var s = b.status;
    if (b.started) s += ' &middot; started ' + esc(b.started);
    if (b.finished) s += ' &middot; finished ' + esc(b.finished);
    return s;
  }

  function renderBook(d, slug) {
    var grouped = regroup(d.books);
    var reads = null;
    for (var k in grouped.groups) {
      if (grouped.groups[k][0].slug === slug) { reads = grouped.groups[k]; break; }
    }
    if (!reads) {
      var one = d.books.filter(function (b) { return b.slug === slug; });
      if (!one.length)
        return { title: 'not found', html: "<h1>Not in the log</h1>"
          + "<div class='meta'>This book is no longer in the reading log. "
          + "<a href='index.html'>Back to the diary</a>.</div>" };
      reads = one;
    }
    var base = reads[0], single = reads.length === 1, out = [];
    var links = ["<a class='back' href='log.html'>log a session</a>"];
    if (single)
      links.push("<a class='back mt-edit' href='#' data-slug='"
        + esc(base.slug) + "'>edit</a>");
    links.push("<a class='back' href='#' id='mt-readagain' data-slug='"
      + esc(base.slug) + "'>read again</a>");
    out.push(links.join(' &middot; '));

    var img = base.cover
      ? "<img class='cover' src='" + esc(base.cover) + "' alt='"
        + esc(base.title) + " cover'>"
      : "<div class='bignoimg' style='background:hsl(" + base.hue
        + ",35%,32%)'>" + esc(base.title) + '</div>';
    var bits = ['<h1>' + esc(base.title) + '</h1>'];
    if (base.author) bits.push("<div class='meta'>" + esc(base.author) + '</div>');
    if (single) {
      if (base.rating != null)
        bits.push("<div style='margin-top:6px'>" + stars(base.rating) + '</div>');
      bits.push("<div class='meta' style='margin-top:6px'>"
        + statusLine(base) + '</div>');
    } else {
      bits.push("<div class='meta' style='margin-top:6px'>" + reads.length
        + ' reads</div>');
    }
    if (base.pageCount)
      bits.push("<div class='meta'>" + base.pageCount + " pages <span title='source'>("
        + esc(base.pageSource || '') + ')</span></div>');
    out.push("<div class='head'>" + img + '<div>' + bits.join('') + '</div></div>');

    reads.forEach(function (read, idx) {
      if (!single) {
        out.push("<div class='readsec'>");
        out.push('<h2>Read ' + (idx + 1) + " <a class='back mt-edit' href='#' "
          + "data-slug='" + esc(read.slug) + "'>edit</a></h2>");
        var meta = statusLine(read);
        if (read.rating != null) meta += ' &middot; ' + stars(read.rating);
        out.push("<div class='meta'>" + meta + '</div>');
      }
      var perDay = dailyPages(read);
      if (read.sessions.length) {
        if (single) out.push('<h2>Sessions</h2>');
        out.push('<table><tr><th>date</th><th>at page</th><th>pages</th></tr>');
        var prev = 0;
        read.sessions.forEach(function (s) {
          var delta = Math.max(0, s[1] - prev);
          prev = Math.max(prev, s[1]);
          out.push('<tr><td>' + s[0] + '</td><td>' + s[1] + '</td><td>'
            + delta + '</td></tr>');
        });
        out.push('</table>');
        var days = Object.keys(perDay).sort();
        var peak = 0;
        days.forEach(function (dd) { peak = Math.max(peak, perDay[dd]); });
        peak = peak || 1;
        var bars = days.map(function (dd) {
          var h = Math.max(2, Math.round(perDay[dd] * 100 / peak));
          return "<div class='b' style='height:" + h + "%' title='" + dd + ': '
            + perDay[dd] + " pages'><span>" + perDay[dd] + '</span></div>';
        }).join('');
        out.push("<div class='chart'>" + bars + '</div>');
      }
      if (!single) out.push('</div>');
    });
    return { title: base.title, html: out.join('') };
  }

  // A list edited seconds ago: the saved rows are the truth, and covers are
  // pulled across from the build by title|author. A row added just now has
  // no cover yet and tiles, exactly as it did under the static build.
  function mergeList(blist, stem) {
    var all = lsGet(PENDL) || {};
    var pend = all[stem];
    if (!pend) return { list: blist, syncing: false };
    if (Date.now() - (pend.savedAt || 0) > PEND_TTL_MS) {
      delete all[stem]; lsSet(PENDL, all); return { list: blist, syncing: false };
    }
    var known = {}, i;
    for (i = 0; blist && i < blist.items.length; i++)
      known[cacheKey(blist.items[i].title, blist.items[i].author)] = blist.items[i];
    var same = blist && blist.items.length === pend.items.length;
    var items = pend.items.map(function (it, idx) {
      var k = cacheKey(it.title, it.author), e = known[k] || {};
      if (same && blist.items[idx]
          && cacheKey(blist.items[idx].title, blist.items[idx].author) !== k)
        same = false;
      return { title: it.title, author: it.author || '',
               cover: e.cover != null ? e.cover : null,
               hue: e.hue != null ? e.hue : tileHue(it.title),
               href: e.href || null, rating: e.rating == null ? null : e.rating };
    });
    if (same) { delete all[stem]; lsSet(PENDL, all); return { list: blist, syncing: false }; }
    return {
      list: { stem: stem, title: (blist && blist.title) || stem,
              ranked: blist ? blist.ranked : true, items: items },
      syncing: true
    };
  }

  function saveListPending(stem, items) {
    var all = lsGet(PENDL) || {};
    all[stem] = { savedAt: Date.now(), items: items };
    lsSet(PENDL, all);
    var snap = lsGet(SNAP);
    if (snap) { try { paint(snap); } catch (e) { /* the save still worked */ } }
    polls = 0;
    poll();
  }
  window.mtSaveListPending = saveListPending;

  function renderListGrid(l, stem) {
    var blist = null;
    for (var i = 0; i < l.lists.length; i++)
      if (l.lists[i].stem === stem) { blist = l.lists[i]; break; }
    var merged = mergeList(blist, stem);
    blist = merged.list;
    if (!blist)
      return { title: 'not found', html: '<h1>No such list</h1>'
        + "<div class='meta'><a href='./'>All lists</a></div>" };
    var out = [];
    out.push("<a class='back' href='./'>&larr; all lists</a> &middot; "
      + "<a class='back' href='edit.html?list=" + esc(blist.stem) + "'>edit</a>");
    out.push('<h1>' + esc(blist.title) + '</h1>');
    out.push("<div class='meta'>" + blist.items.length + ' '
      + (blist.ranked ? 'titles, ranked' : 'titles') + '</div>');
    out.push("<ol class='grid'>");
    blist.items.forEach(function (item, idx) {
      var badge = blist.ranked ? "<span class='rank'>" + (idx + 1) + '</span>' : '';
      var img;
      if (item.cover) {
        img = "<img class='cov' src='" + esc(item.cover) + "' loading='lazy' alt='"
          + esc(item.title) + " cover'>";
      } else {
        var a = item.author ? "<div class='na'>" + esc(item.author) + '</div>' : '';
        img = "<div class='noimg' style='background:hsl(" + item.hue
          + ",35%,32%)'><div class='nt'>" + esc(item.title) + '</div>' + a + '</div>';
      }
      var cap = item.author ? "<div class='a'>" + esc(item.author) + '</div>' : '';
      var body = img + "<div class='cap'><div class='t'>" + esc(item.title)
        + '</div>' + cap + '</div>';
      var rate = '';
      if (item.href) {
        if (item.rating != null)
          rate = "<span class='rate' title='" + g(item.rating) + "/5'>★ "
            + g(item.rating) + '</span>';
        body = "<a class='tl' href='" + esc(item.href) + "'>" + body + '</a>';
      }
      out.push("<li class='tile'>" + badge + rate + body + '</li>');
    });
    out.push('</ol>');
    return { title: blist.title, html: out.join(''), syncing: merged.syncing };
  }

  function renderListsIndex(l) {
    var out = ['<h1>Lists</h1>',
      "<a class='back' href='edit.html?new=1'>+ new list</a>",
      "<ul class='lists'>"];
    l.lists.forEach(function (b) {
      out.push("<li><a href='" + esc(b.stem) + ".html'>" + esc(b.title)
        + "</a> <span class='meta'>(" + b.items.length + ') &middot; '
        + "<a href='edit.html?list=" + esc(b.stem) + "'>edit</a></span></li>");
    });
    out.push('</ul>');
    return out.join('');
  }

  // ---------------------------------------------------------------- boot

  function paint(snap) {
    var page = document.body.getAttribute('data-page');
    var root = document.getElementById('mt-root');
    if (!root) return;
    var diary = snap.diary, lists = snap.lists;

    if (page === 'calendar' || page === 'flatlist' || page === 'book') {
      if (!diary) return;
      var pend = reconcile(diary);
      var books = mergeBooks(diary, pend);
      regroup(books);
      var view = { books: books, films: diary.films || [],
                   dailyGoal: diary.dailyGoal || 0 };
      if (page === 'calendar') {
        root.innerHTML = renderCalendar(view).html;
        wireMonthNav();
      } else if (page === 'flatlist') {
        root.innerHTML = renderFlatList(view);
        if (window.mtListPage) window.mtListPage();
      } else {
        var res = renderBook(view, document.body.getAttribute('data-slug'));
        root.innerHTML = res.html;
        document.title = res.title;
        if (window.mtBookPage) window.mtBookPage();
      }
      if (pend) setStatus('pending', 'saved · rebuilding…');
      else setStatus('ok', 'up to date');
    } else if (page === 'list') {
      if (!lists) return;
      var r = renderListGrid(lists, document.body.getAttribute('data-stem'));
      root.innerHTML = r.html;
      document.title = r.title;
      if (r.syncing) setStatus('pending', 'saved · rebuilding…');
      else setStatus('ok', 'up to date');
    } else if (page === 'lists-index') {
      if (!lists) return;
      root.innerHTML = renderListsIndex(lists);
      setStatus('ok', 'up to date');
    }
  }

  var polls = 0;
  function poll() {
    // Only while an edit is outstanding: the page refreshes ITSELF when the
    // build lands, which is the whole point — no manual reloading.
    var listsPending = lsGet(PENDL);
    var any = lsGet(PEND) || (listsPending && Object.keys(listsPending).length);
    if (!any || polls > 40) return;
    polls++;
    setTimeout(function () {
      if (document.hidden) return poll();
      cycle().then(poll);
    }, 15000);
  }

  function cycle() {
    var snap = lsGet(SNAP);
    return refresh(snap).then(function (next) {
      if (next) { lsSet(SNAP, next); paint(next); }
      return next;
    }).catch(function () { /* offline: the snapshot still stands */ });
  }

  function boot() {
    if (!document.body.getAttribute('data-page')) return;  // log.html etc.
    var snap = lsGet(SNAP);
    if (snap && (snap.diary || snap.lists)) paint(snap);   // instant, no network
    else setStatus('pending', 'loading…');
    cycle().then(poll);

    // Re-check whenever the phone comes BACK to this page. Without these
    // two, a page can sit on stale data indefinitely:
    //   - pageshow/persisted: iOS Safari restores a back-navigation from
    //     the bfcache and re-runs NO script, so boot() never fires again
    //     and the page shows exactly what it showed when you left it.
    //   - visibilitychange: the save-then-switch-apps-then-come-back flow,
    //     and any change that wasn't yours (Letterboxd sync, another
    //     device) — the poll loop only runs while YOUR edit is outstanding.
    window.addEventListener('pageshow', function (e) {
      if (e.persisted) { polls = 0; cycle().then(poll); }
    });
    document.addEventListener('visibilitychange', function () {
      if (!document.hidden) { polls = 0; cycle().then(poll); }
    });
  }

  if (document.readyState === 'loading')
    document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
