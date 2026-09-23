# External and manual inventory

The capacity gate sums every nonflat broker position's absolute market value, including external/manual inventory. Global slot reservation also starts from all nonflat broker positions and OPEN/reserved order claims. The existing ownership and overlay rules continue to prevent managed growth of pure external positions and do not create a bot SELL for external-only inventory. Over-limit external gross blocks new BUYs while a managed risk-reducing SELL stays available. The dashboard exposes current realized gross, occupied slots and suppression reason; no forced liquidation was added.

Evidence: corrected safe G1/G2/G3 tests, shared gate tests, and existing mixed-ownership/global-slot suites. The synthetic G5 pre-fix audit showed external exposure at gross 0.605; corrected G1/G2 fixtures include the same external BTC position and assert no submitted BUY.
