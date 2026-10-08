# Game Cafe Console — Consolidated Product Specification

**Version:** 1.1 (distributed session reliability)  
**Date:** 8 October 2026  
**Status:** Product behavior agreed in discussion; not an implementation contract for unresolved technical details  
**Audience:** Café owner, designer, developer, and future implementation agents

> **Source-of-truth rule:** This specification consolidates the earlier Phase 1 (Onboarding) and Phase 2 (User Console) documents with all subsequent decisions about the Admin Console, buffer time, grace, session ending, offline status, and automatic sign-out. **Where an earlier document conflicts, this document takes precedence.** In particular, the old *15-minute grace* and *automatic Windows logout immediately after grace* are obsolete.

---

## 1. Product vision and non-negotiable principles

Game Cafe Console is a **local-network Windows gaming café management system**. The café owner (Sahil in the examples) manages multiple gaming PCs without depending on an external cloud service.

1. **One software package for every PC.** Any registered PC may operate as a customer PC or, with the proper authorization, become the active admin console. There is no permanently designated admin hardware.
2. **One intended café pool.** Every café has a pool with a permanent identity. PCs discover and synchronize with their pool over the LAN.
3. **One active admin at a time.** The active admin controls permission to use customer PCs, time assignments, membership approvals, and authoritative shared updates.
4. **An Admin authorizes new grants, but a User PC owns its existing session.** An authorized session continues locally while the Admin is offline; no new paid-time grant is possible until an Admin is reachable.
5. **Same-PC normal Windows usage during access.** Games, browsers, Discord, and other applications work normally while the customer is authorized.
6. **Separate Win32 desktops for access control.** A dedicated `CafeConsole` desktop contains the café interface; the Windows `Default` desktop contains games and ordinary applications. Switching desktops does not close background applications.
7. **Grace preserves a session.** A timed session reaching zero first enters grace; it is *not yet a completed session*. Adding time during grace resumes the same session.
8. **Ending a session does not automatically terminate games.** The user returns to locked CafeConsole. Games can continue running on the hidden Default desktop.
9. **Bounded, replicated local data.** Recent history and necessary shared state are stored on multiple pool PCs, not solely on the current admin PC. Synchronization is acknowledged and retried where required.
10. **No game tampering.** Do not inject into games, manipulate game memory, or rely on global input interception to implement ordinary access control.

This is a **product/workflow specification**, not a Codex implementation prompt or a mandate to build every capability in one step.

---

## 2. Vocabulary and identity

| Term | Meaning |
|---|---|
| **Pool / café pool** | The group of installations belonging to one café and sharing café data over its LAN. |
| **Pool ID** | Permanent, system-generated identifier; never inferred from the café's display name. |
| **Café name** | Editable, human-readable display label for a pool. |
| **PC ID** | Permanent, normally hidden identifier for an installation/machine. Survives PC renaming and an authorized pool change. |
| **PC name** | Editable display name, initially suggested as `PC-01`, `PC-02`, etc. |
| **Default desktop** | The ordinary Windows desktop with games and user applications. |
| **CafeConsole desktop** | A separate Win32 desktop with the customer-facing locked/unlocked café interface. |
| **Admin Console** | The administrator UI, running on the ordinary Default desktop. |
| **Session** | One period of customer access, with a player name, start/end, time details, and final history record. |
| **Timed session** | A session with assigned paid minutes; it may have a separate initial buffer period. |
| **No-timer session** | An open-ended session with elapsed usage counted upward, until the customer or admin ends it. |
| **Buffer time** | Optional initial startup allowance, selected separately for each new session; excluded from paid/chargeable time. |
| **Grace time** | Configurable interval *after paid time expires* when access is locked but the same timed session can still be renewed. |
| **Locked, no session** | CafeConsole is shown, no active session and no grace period; eligible for optional delayed Windows sign-out. |
| **Online/offline** | Indicates whether Game Cafe Console's agent is communicating, **not** necessarily whether the physical PC is powered on. |

The **player/session name** is separate from the **PC name** and is **not a permanent customer account** in the current agreed workflow.

---

## 3. Phase 1 — First launch, onboarding, and pool membership

### 3.1 Startup discovery

On each software launch, read any locally saved PC ID and pool membership, then search the LAN for Game Cafe Console pools.

**Registered PC, own pool reachable:**

- Reconnect automatically; synchronize relevant shared data and any renamed PC label.
- Enter the normal café console flow without repeating onboarding.
- Do not switch pools simply because another pool is discovered.

**Registered PC, own pool currently unreachable:**

- Preserve its identity, data, and saved pool membership.
- Show a connection/retry state; **never silently create a replacement café** or reset onboarding.

**Fresh/unregistered PC, one or more pools discovered:**

- Show available café names and enough of each pool ID to disambiguate duplicates.
- The person setting up the PC selects a pool and presses **Request to Join**.
- The pool's active admin sees and accepts/rejects the request.
- Until accepted, the PC remains unregistered/pending.
- After acceptance, register the permanent PC ID, assign/confirm an editable default PC name, copy/sync pool data, and complete onboarding.
- A known PC reconnecting to its existing pool **does not request approval again**.

**Fresh PC, no pool discovered:**

Display **No café found**, with a helpful explanation: other café PCs may be switched off, disconnected, or not running the software. Offer **Search Again** and **Create New Café**. **Never automatically create a pool merely because discovery returned nothing.**

### 3.2 Creating the first pool

Choosing **Create New Café** launches short setup:

1. Generate and save a permanent **pool ID**.
2. Ask for the **editable café name**.
3. Generate and save this PC's permanent **PC ID**.
4. Suggest visible name **`PC-01`**, editable during setup.
5. Ask for the initial **admin display name**.
6. Ask for an **admin password**, then ask again for confirmation.
7. Verify the passwords match, securely store authentication data **without storing plaintext passwords**, and create the first admin.
8. Save the new pool and register the first PC.
9. Enter the normal café console flow.

### 3.3 PC naming and identity

- A new member gets the next appropriate proposed label, e.g. `PC-07`, and may be renamed to `VIP-1`.
- **PC IDs are immutable across display-name changes and authorized moves between pools.**
- Other PCs are not renumbered if one PC disconnects or is removed.
- When an offline PC reconnects, it receives any newer admin-edited display name.
- Default names are merely suggestions; the permanent PC ID is what the software tracks.

### 3.4 Accidental duplicate pools and changing cafés

The intended operation is **one pool per café**, but two pools can temporarily exist if the real pool was unavailable and another was mistakenly created. Never silently merge pools or delete another pool across the LAN.

From **Admin Settings → Café Management → Change Café**, an admin may deliberately move a registered PC:

1. Search for and display distinct candidate café **names and pool IDs**.
2. Explicitly select and confirm the destination.
3. Obtain approval/authorization from the **destination pool's active admin**.
4. Connect, download and **successfully save** the required destination data.
5. Register the PC under its **existing permanent PC ID** and confirm synchronization.
6. Change locally saved membership.
7. **Only after successful transfer**, remove the former pool's *local copy on that PC*.

If any step fails, keep/recover old membership and data so the PC is not stranded halfway. **Changing a PC's pool never remotely deletes that pool from other PCs.** Retirement of an accidental pool across its remaining machines is a separate, deliberate admin operation to design later.

---

## 4. Software roles, desktops, and first entry

### 4.1 One application with multiple responsibilities

The same installation supports customer/client behavior and, after authorization, Admin Mode. The Win32 desktop implementation may use an invisible/background controller and a separate child GUI process on `CafeConsole`; these are internal parts of **one installed product**, not separate products for the owner to install.

- **Customer PC:** `CafeConsole` controls access; `Default` contains games and Windows apps.
- **Admin PC:** Admin Console UI runs on `Default`.
- **Active admin role:** Can move between registered machines, but only one PC may hold it at a time.

The final precise post-onboarding navigation and admin login/takeover screens have not yet been designed; avoid inventing a mandatory double login or a particular splash-screen layout.

### 4.2 Desktop access rules

- **Locked:** user sees `CafeConsole`; common Windows shortcuts do not expose applications on `Default` through normal navigation.
- **Unlocked:** user can switch in **both directions** between `CafeConsole` and `Default`.
- Desktop switching **does not close or suspend games**.
- A small Default-desktop timer/navigation control should provide access back to CafeConsole and show available session information. Visibility above **exclusive fullscreen games is not guaranteed**. Do not inject into game processes to force such a display.
- On the dedicated CafeConsole desktop, the console itself displays the relevant information and controls.

### 4.3 Role and authority

- New clients require admin approval to join.
- One admin is active for the pool; any eligible PC may become admin after authentication.
- If an admin is already active elsewhere, a request to transfer/take over can be made with proper admin authorization. Old admin control must be revoked before the new admin becomes authoritative.
- No permanent designated admin laptop/PC is required.
- Authentication mechanics, transfer confirmations, stale-admin handling and failover protocol need their own implementation design.

---

## 5. Phase 2 — User Console

### 5.1 Locked screen

Display a clear **PC LOCKED** state. Main customer action:

**REQUEST UNLOCK**

- Sends a request/notification ("ping") to the active admin.
- **Does not** itself unlock the PC, even when pressed repeatedly.
- Admin decides whether to grant access.
- Show simple feedback such as *Request sent*; a brief anti-spam cooldown is a UI detail.

Also provide a separate, less prominent **Close Café Software (Admin)** action (see §9). It requires the admin password and is not a customer unlock mechanism.

### 5.2 Unlocked state

Once access is authorized, the customer can freely switch between both desktops. The console and accessible Default-desktop control show:

- PC name, e.g. `PC-07`.
- Session player name, initially **Guest**.
- Timed session: **remaining paid time** and **total paid time assigned**; where applicable, clearly show remaining buffer or grace instead of mislabeling it as paid time.
- No-timer session: **elapsed counted usage** rather than a countdown.
- **END SESSION** action.

The admin controls assignments and can see current user/session information. Customers cannot grant themselves more time by navigating desktops.

### 5.3 Editable per-session player name

- Every **new session** starts with name **`Guest`**.
- Customer may rename it (e.g. `Sumit`).
- Admin sees the new name on that PC's live entry; relevant state is synchronized.
- The **final name** at completion is stored in session history, even if the session originally began as Guest.
- During grace, this is still the **same session**, so do **not** reset the name at paid-time zero.
- After the session is *actually finalized*, the next session starts again as **Guest**.

A separate persistent customer account/login system is **not defined for this version**.

### 5.4 Customer ends session

Customer uses **END SESSION** → session finalizes **immediately with no grace** → history is recorded → CafeConsole becomes locked. Background Windows applications remain running on `Default`.

---

## 6. Phase 3 — Admin Console

The Admin Console runs on the **normal Windows desktop**, and its intended information architecture contains three sections.

### 6.1 Main section — Dashboard

A **two-part main screen**:

**Left: list of all PCs.** Each PC has a generously sized card/row with relevant controls and live status. Depending on state, show:

- Visible PC name.
- Agent **ONLINE / OFFLINE** (offline means no console/agent contact, not necessarily PC power-off).
- **LOCKED / UNLOCKED** and session phase (buffer, timed, no-timer, grace, or no session).
- Live player name (`Guest` or renamed name).
- Remaining paid time, elapsed no-timer usage, or grace countdown; buffer indicator when applicable.
- Assigned paid-time total where relevant.
- State-appropriate controls, such as **Start Session**, **Add Time**, **Lock / End Session**, or **Unlock**, and access to any pending unlock request.

**Right: history.** Clicking a PC on the left updates the right side to show that PC's recent completed sessions, including **final player name** and **total session time/assignment**. There should also be a way to review recent history **across all PCs**. We have deliberately not finalized the exact history design or visual styling.

### 6.2 Computers / Connections section

For managing pool membership and network participation:

- Connected/registered PCs.
- Pending **Request to Join** entries, with **Accept / Reject**.
- PC identity/display names and connectivity.
- Other connected-device management to be determined at implementation/design time.

A request to unlock a **locked registered PC** is different from a **new PC asking to join the pool**. Their admin-side presentations and permissions should remain separate.

### 6.3 Settings section

At minimum, Admin Settings should support:

- Change admin password and appropriate admin account details.
- Edit café name and PC names as appropriate.
- Café/pool management, including deliberate change of pool.
- **Grace period duration** (initial default **10 minutes**, configurable).
- **Automatic Windows Sign-Out**: independent on/off switch and configurable delay (initial example/default **30 minutes**).
- Any future settings agreed during implementation, without assuming they already exist.

**Inactivity notification** may be considered an optional, low-priority informational setting; see §10. It must never silently change paid access.

### 6.4 Admin locking a PC

- If the PC has **no active session/grace**, locking simply keeps/puts it in locked CafeConsole.
- If there is an **active timed/no-timer session or a session still in grace**, first show explicit confirmation with PC name, current player, session details and time information.
- Confirming **ends the session immediately**, stores its final history, and locks the PC. This is **not pause**. No grace is added after an admin-forced end.
- If the admin cancels, the session remains unchanged.

### 6.5 Unlocking and session creation

- Admin can manually control lock/unlock and start a new customer session on a selected PC.
- Starting a timed session asks for **paid minutes** and optional **buffer minutes**.
- Starting a no-timer session allows optional **buffer minutes**, then counts usage upward.
- Exact UI (modal, expanded PC card, etc.) is left for later design.
- A distinct **unlocked but no session** state has been mentioned for administrative flexibility, but whether and how it differs from starting a no-timer session is **not fully settled**. See §13.

---

## 7. Session timing: buffer, paid time, no-timer, and adding time

### 7.1 Timed session with buffer

At session start, Sahil may specify both:

- **Paid assigned time** (example: 60 minutes).
- **Optional buffer** (example: 5 minutes).

Buffer begins **immediately**, before the paid-time countdown. The user has authorized access while buffer runs.

Example:

| Clock | Phase | Paid time balance |
|---|---|---|
| 14:00 | Session starts; 5-minute buffer starts | 60 min, not yet counting down |
| 14:05 | Buffer ends; paid time starts | 60 min |
| 15:05 | Paid time reaches zero | Enter grace |

**Buffer minutes are not added to the total paid time assigned**, though actual session elapsed time can separately record them. Admin and user should clearly see when buffer is active and when paid time will begin.

### 7.2 Adding paid time during buffer or active paid time

- During **buffer**, new paid minutes increase the upcoming paid allotment; **the original buffer continues uninterrupted**.
- During an active timed countdown, added time **stacks onto the remaining paid time**.

Example: originally 60 assigned, 22 remaining, +30 added → **52 remaining**, **90 total paid minutes assigned**.

### 7.3 No-timer (open-ended) session

A no-timer session is an **ordinary session without assigned countdown/expiry**, not an entirely different product mode.

- Optionally give buffer at the start.
- Count actual time used upward; distinguish the initial buffer from counted usage.
- Remain unlocked until customer/admin ends it.
- Record elapsed counted time in final history.
- **No Add Time control** is offered for a no-timer session, because there is no paid countdown to extend.
- The exact charging model is outside this specification; the purpose is to know how long the PC was used.

### 7.4 Paid timer reaches zero: grace, not completion

- Switch the machine to **locked CafeConsole**.
- Begin the **configurable grace countdown** (initially 10 minutes).
- Keep the **same session open**, including its player name and prior assignments.
- Games/apps on `Default` **continue running**.
- Show grace remaining to the admin and user, and permit the admin to extend the timed session.
- Do **not** write the final completed-session history yet.

**Time added during grace starts at the moment it is added**, without charging or deducting the elapsed grace interval:

| Clock | Event |
|---|---|
| 14:00 | Timed session begins with 60 paid minutes (no buffer in this example) |
| 15:00 | Paid time ends; PC locks; grace starts |
| 15:05 | Admin adds 60 minutes; PC unlocks; grace stops |
| 16:05 | Those new 60 minutes expire if not extended |

This is **one continuing session** with **120 paid minutes assigned**, plus 5 minutes of grace used. Final history is created only when the session truly ends.

- If the admin **ends the session during grace**, finalize immediately; no further grace.
- If **grace expires**, finalize the session, save history, reset the name for the *next* session, and remain locked.

### 7.5 Source-of-truth time semantics

Time additions, start times, phases and finalization must have an authoritative record coordinated by the active admin, rather than being independently editable by customers. Live countdowns can be displayed locally, but need to reconcile with shared state. How clock synchronization and recovery work is an engineering decision for later.

---

## 8. Session completion, history, and what happens to games

### 8.1 Events that finalize a session

| Cause | Grace? | Finalize history? | Immediate visible result | Games running on Default? |
|---|---|---|---|---|
| Customer selects **End Session** | **No** | Yes, immediately | CafeConsole locked | **Yes** |
| Admin confirms **Lock / End** for a live session | **No** | Yes, immediately | CafeConsole locked | **Yes** |
| Timed session enters grace at paid time zero | Grace begins | **Not yet** | CafeConsole locked, renewal possible | **Yes** |
| Admin ends session during grace | **No more grace** | Yes, immediately | CafeConsole remains locked | **Yes** |
| Grace expires without renewal | Grace completes | Yes | CafeConsole remains locked | **Yes** |
| Admin adds time during grace | Grace stops | **No** — same session resumes | Default desktop becomes accessible | **Yes** |

**Deliberate decision:** Do **not** automatically use Windows Lock (`Win+L`) at a session end; it gives no benefit over the CafeConsole lock for this goal. Do **not** automatically Windows-sign-out immediately at grace expiry. Leaving background games alive is accepted behavior.

### 8.2 Session history vs time-assignment history

One complete session may include many `+30`/`+60` assignments. **Do not treat each time addition as a separate completed session.** A final record should be capable of representing:

- Permanent **PC ID** and display name (as appropriate).
- Unique session ID.
- Final player name (e.g. `Sumit` even if session began `Guest`).
- Start and end date/time.
- **Total paid time assigned** for timed sessions; **counted elapsed time** for no-timer sessions.
- Separate optional buffer allowance/actual buffer usage.
- Grace time used, if any, separate from paid time.
- Actual session elapsed duration, if useful.
- Reason session ended (customer, admin, grace expiry).
- Associated time-addition events, if retained.

The main admin history should at least surface **player name and session total**, per the agreed dashboard design. Detailed fields and the visual layout will be chosen later.

### 8.3 Retention

History should remain **small and bounded** on every PC. The earlier suggested limit is **approximately 20 recent records** (potentially recent sessions per PC with embedded additions). The final count and whether it applies to sessions, events, or both **have not been formally frozen**; avoid claiming unlimited archival data.

### 8.4 Background application consequence

When CafeConsole locks, **Valorant, Chrome, Discord, Steam, etc. can remain open on Default**. This makes renewal fast and preserves game state during grace. It also means a later customer may encounter apps/accounts left by the earlier customer if staff begins a new session without resetting the machine. This trade-off is **accepted in the current product decision**; no automatic game-closing feature is required by normal session end.

---

## 9. Local, password-protected Close Software action

The locked CafeConsole provides a secondary **Close Café Software (Admin)** control, **not** an admin-password *unlock* that leaves the café controller active.

When pressed:

1. Ask for the admin password and verify authority.
2. If any session is still active or in grace, finalize it and save/synchronize the history as safely as possible.
3. Switch to the normal `Default` desktop **before** stopping any component needed for desktop recovery.
4. Shut down the CafeConsole child and local controller cleanly.
5. Stop communicating with the pool.

The admin dashboard should simply transition this PC to **agent/console OFFLINE** when heartbeats stop; no separate special "software closed" state or attribution is required. The PC itself may remain powered on.

External forcible termination through Task Manager is a known limitation: the dashboard reports lack of contact, **not** whether a customer deliberately closed the app. The system is not required to make every process impossible to kill.

---

## 10. Optional automatic Windows sign-out and inactivity information

### 10.1 Automatic sign-out after prolonged locked idle state

This is a **separate, optional Admin Settings feature**, with:

- **Enable / disable** switch.
- Configurable **sign-out delay** (initial example/default: **30 minutes**).

**Trigger condition:** The PC has been on the locked CafeConsole with **no running session and no active grace period** for the configured interval.

This is **not AFK detection**. It must not inspect keyboard/mouse/controller use to decide whether a session should end.

Examples:

- Timed session paid time ends at **15:00**, 10-minute grace expires at **15:10**, sign-out delay is **30 minutes** → eligible to sign out at **15:40**, provided the PC stayed locked without another session.
- Customer manually ends a session → no grace; eligible countdown starts when the PC enters locked/no-session state.
- PC starts already locked without a session → eligible countdown starts from its locked/no-session state.
- A new session starts before the delay → cancel/reset the sign-out countdown.

**Windows Sign Out is not the same as Windows Lock.** Sign-out closes games **and also ordinary software running within that Windows user session**, potentially including our current user-session controller/UI. It does **not** switch off the PC or eliminate all electricity use. After sign-out, the admin may see this console **OFFLINE** until the software is restarted or recovered.

**Do not enable this in production before engineering and testing a reliable way to restart/restore the café controller after sign-out.** The concrete service/login/session-recovery mechanism remains undecided.

This function must never fire while a session is still timed, no-timer, in buffer, or in grace. It is a cleanup mechanism for **locked/no-session** PCs only.

### 10.2 Optional possible-inactivity notification (non-enforcing)

Sahil also mentioned a possible **informational admin ping** when a PC with an active session appears unused for a while (example: **20 minutes**).

- **Do not build a dedicated controller-by-controller or game-specific AFK detector.**
- If available **existing/simple Windows input-activity information** is sufficient, the app *may* surface **possible inactivity** in Admin Console.
- Such information is inherently imperfect: a controller or other input path may not be captured.
- **Never pause, lock, sign out, end, or deduct paid time because of this indicator.**
- Treat this as **optional/deferred**, not part of the required first release or an already proven capability.

---

## 11. Local networking, online status, shared storage and admin authority

### 11.1 LAN-only topology

- Every PC runs the same installed application.
- Discover peers/pools on the café's LAN; no internet/cloud is required for core operation.
- The active admin PC authorizes management changes and coordinates replication across the pool.
- Clients locally perform their own Windows desktop switches after authenticated/authorized commands; the admin PC does **not** remotely call `SwitchDesktop` on another PC.
- Client PC heartbeat/status lets admin see **ONLINE / OFFLINE**, lock state, current session phase, and timers.

### 11.2 Shared small dataset

The pool needs synchronized information such as:

- Pool ID and café display name.
- Registered PC IDs, PC names and current membership.
- Necessary **non-plaintext** admin authentication information and authorization state.
- Current/last-known session status and time assignments.
- Bounded recent completed-session history and related updates.
- Relevant pool settings such as grace period and auto sign-out configuration.

Every active pool PC retains the appropriate shared local data so admin control can move to another machine. Ordinary customer-facing UI **does not get access to administrative records just because a local protected copy exists**.

### 11.3 Synchronization and acknowledgement

- The **active Admin authorizes privileged commands**; each User PC owns and persists its own session state and completion history.
- User-originated session records are signed by the registered PC identity and merged by owner version. Admin grants require authenticated commands. Persist before acknowledging; retry missed records after reconnect.
- Track missed updates for offline/unresponsive PCs and retry when they reconnect.
- Use stable event/update identifiers or revisions so receivers can detect duplicates and gaps.
- Pool snapshots synchronize shared settings and membership; they must not replace a newer User-owned session record or revive a completed session.
- Distinguish "agent OFFLINE" from a certain machine-power state or deliberate user action.
- The exact conflict, admin-election, revision, security, and recovery protocol is **not finalized**; it must be designed before production.

SQLite or another very small local store is a suitable future implementation option; **no shared SQLite file over a network drive** and no large/cloud database are required. Storage engine selection has not yet been frozen.

### 11.4 Existing feasibility results (not final production protocol)

Earlier Windows/Python experiments validated:

- Fullscreen Tkinter lock window and ignoring its normal Alt+F4 close request.
- Creating a separate `CafeConsole` **Win32 desktop** and switching safely to/from the Windows `Default` desktop.
- Keeping a visible test return path and safe local exit during experimentation.
- Running the same packaged Windows test executable on two PCs, with LAN status plus **remote LOCK/UNLOCK** successfully exercised.
- Automatic discovery/manual IP and client status were demonstrated with prototype UDP/TCP communication.

The prototype happened to use **UDP 47990** and **TCP 47991**; these are **test details, not a permanent product requirement**. The prototype's unauthenticated commands are **not acceptable for final deployment**. Production commands and pool synchronization must have appropriate authorization.

---

## 12. Unified behavior and state transitions

To avoid confusing "locked", "session ended" and "time zero", describe the customer PC using **access state + session state**.

| Current condition | Event | Next condition | History action |
|---|---|---|---|
| Locked / no session | Admin starts timed session with buffer | Unlocked / buffer → active paid timer | Create active session; no final history yet |
| Locked / no session | Admin starts timed session without buffer | Unlocked / active paid timer | Create active session |
| Locked / no session | Admin starts no-timer session | Unlocked / optional buffer → open-ended usage | Create active session |
| Active timed / buffer | Admin adds paid time | Same phase; paid allotment increases | Record addition within current session |
| Active timed / paid countdown | Admin adds paid time | Same phase; expiry extends | Record addition within current session |
| Active timed / paid countdown | Paid time reaches zero | **Locked / grace** | **Do not finalize** |
| Locked / grace | Admin adds paid time | **Unlocked / paid timer restarts from addition instant** | Continue same session |
| Locked / grace | Grace runs out | **Locked / no session** | Finalize once |
| Locked / grace | Admin confirms End | **Locked / no session** | Finalize once |
| Active session | Customer chooses End Session | **Locked / no session** | Finalize once; no grace |
| Active session | Admin confirms Lock/End | **Locked / no session** | Finalize once; no grace |
| Locked / no session | Configured sign-out delay elapses, feature enabled | Windows signs out | No active session should exist |
| Any local running state | Admin-authorized Close Software | Return to Default; software shuts down | Finalize active session if required; become offline |

The admin's **remote lock/unlock control** is separate from the customer's own ability to switch between the two desktops while access is authorized. The exact semantics of unlocking without starting a session remain open (see §13).

---

## 13. Decisions intentionally still open

These are **not contradictions** in the agreed behavior; they are details that should not be silently invented in implementation:

1. **Admin-login/takeover UX:** Exact entry screen, password confirmation steps, handling of stale admin ownership and network partitions.
2. **Admin unavailable mid-session:** Resolved: existing User sessions continue and can be renamed/ended locally; new privileged grants wait for an authenticated Admin. Recovery after process death uses the last local checkpoint, not the whole offline interval.
3. **Unlock without session:** Whether an administrator may leave a PC unlocked with no recorded session, or whether admin unlock necessarily starts a timed/no-timer session. This particularly affects what "unlock" means in an idle PC card.
4. **Automatic sign-out recovery:** Windows service / user-session agent setup, Windows account/login behavior, restarting/locking the console after sign-out, and how to avoid weakening access control.
5. **Bounded history policy:** Exact retention count and whether last ~20 means completed sessions per PC, individual assignment events, or both; detailed history UI.
6. **Cross-pool retirement:** How to explicitly retire/decommission the accidental pool on its other, possibly offline, PCs without deleting unrelated data.
7. **Installation identity:** What happens if a PC is reinstalled, its local identity data is lost, or the same image is cloned to multiple PCs.
8. **Production security:** Authentication for network commands/replication, storage protection, credential change propagation, authorized join and admin transfer, safe retry/fencing.
9. **User-screen details:** Default-desktop widget style, visibility during exclusive fullscreen games, exact error states and validation of guest names.
10. **Optional inactivity indicator:** Whether reliable enough signals already exist to show a non-enforcing possible-inactivity notice. It is not a required lock or billing feature.
11. **Power management:** Windows sign-out reduces game resource use but does not power the PC off; sleep/shutdown is not included in the currently agreed automatic policy.

Do not treat any older document's contrary statement as a requirement when it conflicts with the finalized rules here.

---

## 14. Compact end-to-end examples

### Example A — First café installation

`No café found` → Sahil deliberately chooses **Create New Café** → café name, permanent pool ID, `PC-01` display name, admin name and password twice → first pool saved → CafeConsole flow available.

### Example B — New PC on same LAN

Fresh installation finds `Sahil Gaming Café` → **Request to Join** → active admin accepts → PC ID registered and default `PC-07` name can be edited → pool state synchronized → ready for sessions.

### Example C — Timed session with buffer and renewal

Sahil starts PC-03 at 14:00 with 5-minute buffer and 60 paid minutes → buffer 14:00–14:05 → paid timer 14:05–15:05 → PC enters 10-minute grace at 15:05 → at 15:09 admin adds 30 minutes → same session resumes immediately through 15:39 → no final history written at first expiry.

### Example D — No-timer customer

Sahil opens a no-timer session with optional initial buffer → customer plays while elapsed usage counts upward → customer ends session → one completed record with final player name and counted time → PC locks, games may remain running on Default.

### Example E — Locked cleanup timer

Grace ends at 15:10 → session history finalized → PC remains locked with games still running → optional auto sign-out enabled with 30 minutes → if no new session starts, Windows signs out at 15:40, closing games *and* user-session app components. Reconnection/relaunch needs a separately engineered solution.

### Example F — Admin closes software locally

On locked CafeConsole, Sahil chooses **Close Café Software (Admin)** → supplies admin password → software finalizes any outstanding session, restores `Default`, exits safely → pool stops receiving that PC's heartbeat → admin list shows **OFFLINE**, without guessing why.

---

## 15. Priority for future implementation

This specification is intended to guide staged implementation rather than force a large refactor all at once:

1. Preserve the **already proven** Win32 desktop and LAN prototype behavior.
2. Implement durable PC/pool identity, discover/join/approval, and first admin setup.
3. Build the real User Console states and session model (including buffer, no-timer, grace, manual end).
4. Build the Admin Dashboard, Connections, and Settings flows.
5. Add small local persistence, authoritative state synchronization, acknowledgements/retries, and recent history.
6. Add production-grade admin/command authorization, failure recovery, and optional delayed Windows sign-out **only after it can safely recover**.

## 16. October 2026 distributed-session decisions (supersedes older text above)

- A User PC persists its own authorized session and recent completion records. Existing sessions keep their timer, buffer, grace, player-name editing, local End Session, and desktop access while the Admin is offline. A disconnected User PC is not automatically promoted to Admin.
- Start Session, Add Time, Pause, Resume, and remote End remain privileged active-Admin commands. Pause freezes buffer/paid/open accounting and leaves the Windows desktop accessible; ordinary pause is unavailable in grace. Repeated commands must be idempotent.
- Approximately every minute the User PC saves a local recovery checkpoint. A prior-run unfinished session is finalized on next startup at the last reliable checkpoint, marked interrupted, and never billed across an unobserved outage. A graceful authorized software exit finalizes any active local session first.
- An authenticated active Admin may request **Exit Software** on a reachable User PC after a clear confirmation; this closes Game Cafe Console, not Windows or games. Offline PCs cannot be queued for later exit. Local Staff Access close still requires the Admin password. The Admin PC may exit and relinquish its claim without ending other PCs' sessions.
- Dashboard history cards emphasize PC name, final player name, and actual duration. Start/Add support suggested and custom minutes. Connections separates pending requests from registered PCs; avatar settings have native image browsing and preview. User Staff Access actions are separated and passwords are hidden by default with an optional visibility toggle.
- Automatic Windows sign-out remains disabled. First-available authenticated Admin election remains; arbitrary network partitions can still produce temporary competing claims and do not have perfect conflict resolution.

**End of consolidated specification.**
