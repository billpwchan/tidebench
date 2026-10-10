# 独立交易员与研究员体验审计：v0.13 基线

独立审计结论：v0.13 已经有较完整的研究证据与模拟执行能力，但尚未形成专业用户可以连续工作的交易台。主要差距集中在操作结果闭环、对象上下文连续性、风险信息同屏可见性，以及研究试验的组织方式。继续增加功能或卡片，难以解决这些问题。

这是基线审计，首批实现的状态见单独 v0.14 验收记录。下文建议不表示已经修复，所有源码证据均固定在基线 commit，不对应后续修改后的当前行号。

审阅基线为 2b66f9c0e43fb901888424b8922293624a17b077，审计日期为 2026-10-11，工作树初始干净。reviewer 先检查了 frontend/src 与全部指定 v0.13 截图，没有阅读历史 audit；采用专业交易员和研究员的严苛使用标准，不声称代表任何机构。产品范围是 OKX 公开现货与 USDT 永续行情、研究/回测及 local paper trading，无实盘授权。截图为 synthetic acceptance fixtures，只用于验证布局与交互呈现，不能证明真实账户表现。本 reviewer 环境无法连接 8200；root 已确认服务健康，但浏览器会话过期。因此以下是截图观察与源码核验，没有登录、提交订单、修改账号、读取真实数据库或接触 credentials。无法连接是此次 reviewer 环境限制，不是产品服务失败结论。

以下“观察”指截图或源码直接支持；“推断”指该实现对任务的影响；“待实测”指尚未在有效登录会话验证的行为。P1 表示关键工作闭环受阻，P2 表示明显影响效率或理解，未发现需要宣称为 P0 的产品问题。

1. **P1｜订单提交回执无法带用户准确找到该笔订单及结果。**

   目标用户：连续录入模拟订单的交易员。触发场景：预览并提交市价订单，随后需要核对成交数量、价格、费用和持仓变化。

   观察：成功回调只拼接 Order submitted · id · status，通过页首 ActionNote 显示；没有在 ticket 内展示完整回执或定位链接。账户页默认仍显示持仓，订单默认只显示 working，filled 市价订单被过滤掉。订单搜索字段又不包含订单 ID。[Portfolio.tsx:273](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L273)、[Portfolio.tsx:395](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L395)、[Portfolio.tsx:750](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L750)、[Portfolio.tsx:779](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L779)。

   推断：交易员在 ticket 下方提交后，可能看不到页首回执；即使得到 ID，也必须手动切到订单、切全部结果、再按市场搜索，无法唯一识别该笔订单。

   改进：ticket 内保留独立回执对象，显示市场、方向、请求/成交数量、状态、成交价格、费用与时间，并提供“查看该订单”“查看同市场持仓变化”。显式点击才定位订单或打开结果 inspector。**不要在成功时强制切页签、改搜索条件或替换下一笔草稿**；现有异步回调保护下一笔草稿的代码应保留。

2. **P1｜撤单后记录直接消失，缺少撤单结果闭环。**

   目标用户：巡视大量 working orders 的交易员。触发场景：概览或执行页撤销一笔订单。

   观察：撤单成功仅 invalidates queries；working scope 中被撤销的记录随刷新消失，没有当前行的成功回执、撤销数量、订单定位入口。一个 cancel.isPending 同时禁用所有行的撤单操作。[Portfolio.tsx:285](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L285)、[Portfolio.tsx:883](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L883)、[Overview.tsx:633](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Overview.tsx#L633)。

   推断：记录消失只能说明列表变化，不能让用户确认“这一笔已撤销、撤销了多少、有没有先成交”。这在 300 条 synthetic fixture 中尤其难追踪。

   改进：以订单 ID 维护行级 pending 和最近操作回执；撤单后显示服务器返回的最终状态、已成交数量及撤销剩余，并可打开该订单的结果。无需为每次撤单增加模态确认，但必须保留可核对结果。

3. **P1｜查看市场上下文会离开执行页，并丢失未完成订单草稿。**

   目标用户：先检查行情、再决定操作的交易员。触发场景：执行页已填写下一笔订单，使用 Find a market → Inspect。

   观察：持仓的市场名称只是文本，没有只读检查入口。全局搜索 Inspect 导航到 overview?view=market，随后滚动到概览页末的 MarketContext。执行页订单方向、数量、类型、限价和止损价均为页面 useState，没有跨页草稿持久化。[Portfolio.tsx:170](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L170)、[Portfolio.tsx:652](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L652)、[App.tsx:451](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/App.tsx#L451)、[Overview.tsx:995](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Overview.tsx#L995)。

   静态核验支持：该导航会卸载 Portfolio，返回时订单输入重新初始化。待实测：浏览器后退后的滚动和焦点恢复情况。

   改进：增加独立的只读 market inspector。持仓、订单和搜索均可打开它；它有自己的 inspectedMarket，与 ticket 的 orderMarket 分离。查看 ETH 不应把 BTC 草稿改成 ETH。关闭时返回来源行，保留账簿筛选、排序、滚动和订单草稿。inspector 应呈现已有报价、mark、funding、时间与规则证据，交易动作必须由用户明确选择。

4. **P1｜订单栏展开后，持仓表最重要的风险字段被挤出可见区域。**

   目标用户：持仓巡视并同时操作的交易员。触发场景：1440px 桌面展开 New order。

   观察：[execution 桌面截图](../assets/desk-execution-v0.13-desktop.png) 中，表格在两侧 sticky 市场与长操作列之间仅剩有限宽度；liquidation 标题已被截断，杠杆和 P&L 位于水平滚动区域。源码持仓表有 11 列，未根据 ticket 展开状态调整字段优先级。[Portfolio.tsx:650](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L650)、[table-desk.css:84](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/table-desk.css#L84)。

   推断：用户看到数量、entry、mark、margin，却不能同屏核对风险与盈亏。提示“横向滚动”没有解决巡视的核心要求。

   改进：定义账簿的必要字段集合：市场/方向、数量、mark、P&L、永续风险状态与操作；entry、详细 maintenance、原始 liquidation 类型进入行详情或可选列。把长的 Reduce account position 改为紧凑且明确的 Reduce，并在详情说明账户范围。宽屏可以保留侧栏；中等屏宽优先用 inspector 覆层，或允许用户收起 ticket，避免持续压缩账簿。

5. **P1｜减仓准备操作的影响范围只呈现计数，没有具体受影响对象。**

   目标用户：管理共享账户与组合策略的交易员。触发场景：一个市场存在多个自动控制器或属于多腿 managed group，用户执行 Reduce account position。

   观察：prepare 会停止该市场全部运行控制器并撤销其 entry orders；文案明确说明停止某个 managed leg 会停止整组。然而主区只显示“多少个控制器/多少个订单”，没有控制器名称、组名和受影响的其他腿。inventory owners 放在页面末尾折叠区。[ProtectiveExit.tsx:106](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/ProtectiveExit.tsx#L106)、[ProtectiveExit.tsx:248](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/ProtectiveExit.tsx#L248)、[ProtectiveExit.tsx:386](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/ProtectiveExit.tsx#L386)。

   推断：操作安全限制已有，但用户仍难以理解自己正在停止哪个组合、哪些其他市场会保留仓位。

   改进：准备按钮前显示具体影响清单：控制器/组合名称、将被撤销的订单、整组停止的其他腿，以及保留的 inventory。执行后显示每个对象的结果和未完成步骤。保留 reduce-only、重新核验库存、幂等键与防重新入场机制。

6. **P2｜移动端首屏和单条记录过于冗长，难以进行风险巡视。**

   目标用户：手机上快速检查账户的用户。触发场景：打开 Overview，查找最需要处理的持仓。

   观察：[overview mobile](../assets/desk-overview-v0.13-mobile.png) 在截图底部仍停留于搜索和排序工具，没有出现持仓；[book mobile](../assets/desk-book-v0.13-mobile.png) 一条持仓占据大部分页面。每条记录重复 label、单位、完整动作和 More details；列表还形成 max-height: min(65svh, 640px) 的内部滚动区域。[table-desk.css:134](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/table-desk.css#L134)、[ProWorkspace.tsx:359](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/ProWorkspace.tsx#L359)。

   观察：Overview 移动端只保留 equity 与 gross exposure，cash、margin、P&L 默认全部隐藏。[desk.css:467](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/desk.css#L467)。

   改进：手机主页保留紧凑账户/数据时效行、最优先异常、Positions/Orders 数量和紧凑列表。每条 summary 用市场/方向、数量、P&L、风险状态组织为两三行，详情按需展开；搜索排序合并为简洁工具。优先采用页面单一滚动，订单 ticket 单独作为 sheet。现有 synthetic 来源声明保留，但缩短常驻说明，并可展开完整解释。

7. **P2｜同一对象在多层导航中使用不同名称，操作去向难以预测。**

   目标用户：熟悉策略研究、首次使用此平台的专业用户。触发场景：寻找模拟部署、风险控制、收益归因或策略版本。

   观察：侧栏是 Execution，页面标题为 Portfolio，内部再出现 Portfolio；另有 Account & orders/Risk & limits 两层 tabs。Strategies 既代表侧栏的假设/版本注册表，也代表 Analysis & controls 中的运行策略。Forward performance、Managed portfolios、Paper releases、Ledger 又藏在 disclosure 内。[App.tsx:359](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/App.tsx#L359)、[Portfolio.tsx:365](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L365)、[Portfolio.tsx:522](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Portfolio.tsx#L522)。

   改进：导航明确区分“策略版本”和“模拟部署”。将账户账簿、部署、风险、观测表现作为交易工作区的稳定去向；面包屑显示实际子视图/对象，避免一个 Execution 覆盖全部内容。支持页保留原任务返回上下文。

8. **P2｜研究版本身份存在，但项目名和经济假设没有贯穿试验与发布。**

   目标用户：围绕一个假设不断迭代的研究员。触发场景：Strategies 中保存版本后进入 Research，再选择运行或发布。

   观察：Strategies 明确显示项目名称、版本号与 hypothesis；ProResearch result 主要显示策略类型、run ID、mode、时间，版本身份主要是长 hash。draft binding 也仅显示截短 ID。ResearchRelease 的对象表又主要采用 run ID、selection 和技术字段。[Strategies.tsx:595](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Strategies.tsx#L595)、[ProResearch.tsx:744](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L744)、[ProResearch.tsx:1623](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L1623)、[ResearchRelease.tsx:151](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/ResearchRelease.tsx#L151)。

   改进：贯穿显示“项目名称 · vN · hypothesis 摘要”，技术 ID 为二级证据并可复制。对象链固定为版本→数据包→试验→结果范围→review→paper release→部署，点击任何阶段都返回同一对象上下文。无需另建一套版本体系。

9. **P2｜运行目录不能支撑多试验检索，并在选中后更换呈现方式。**

   目标用户：有几十到几百个试验的研究员。触发场景：找出同一版本、相同时窗或特定成本假设的试验进行比较。

   观察：没有选中运行时使用卡片列表，选中后才变 DataTable；该 DataTable 没有 searchKeys、sortable 或版本/市场/模式筛选。比较 checkbox 位于结果下方的运行目录，目录的表示会随 activeId 改变。[ProResearch.tsx:1189](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L1189)、[ProResearch.tsx:1273](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L1273)。

   改进：固定 trial ledger 表示与位置，加入已有字段的检索/排序：项目/版本、市场、UTC window、evaluation scope、cost assumptions、状态、创建时间。点击一行只改变结果 inspector。比较选择在目录持续可见，保留筛选、选中对象及滚动位置。

10. **P2｜数据选择隐藏识别所需的信息，版本兼容性主要在提交时才提示。**

    目标用户：使用多个市场或多份历史数据版本的研究员。触发场景：给已绑定策略版本选择数据。

    观察：Dataset option 只有 market、bar 和短 hash；覆盖区间只有选中后才能看到，quality 信息在 DataLibrary 而非当前选择器。下拉目录包含当前来源全部 trade datasets，绑定版本的 product/bar 兼容性在 submit() 中才检查。[ProResearch.tsx:1675](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L1675)、[ProResearch.tsx:533](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L533)、[DataLibrary.tsx:259](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/DataLibrary.tsx#L259)。

    改进：把兼容数据/已准备 package 放在首位；选择器展示覆盖区间、版本、完整性和类型。不兼容项明确给出原因；输入区即时呈现版本/数据匹配状态。继续使用现有 package→research handoff 和 UTC intent，避免要求用户重新录入。

11. **P2｜研究首屏突出 Run，却尚未让用户确认完整试验契约。**

    目标用户：认真核对测试范围与成本的研究员。触发场景：手机上创建新试验或载入旧配置。

    观察：[research mobile](../assets/desk-research-v0.13-mobile.png) 在数据字段出现前，已经包含标题、多个页面按钮、Research configuration、Run 条和保存说明。Run 条摘要只有 market、mode、fee/slippage，没有 UTC window、版本、关键参数、数据就绪状态。[ProResearch.tsx:1563](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L1563)。New research 还会直接清除已有本地 draft。[ProResearch.tsx:2064](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L2064)。

    改进：将研究 header、draft identity 和运行动作压成一个紧凑工作栏。执行前让用户核对固定配置摘要：版本、数据/时间、成本、evaluation scope 与 readiness。保留专业用户直接编辑能力，不必改成逐页强制 wizard。新建与清空分开；已有修改可保留为另一个草稿，或提供简短恢复入口。

12. **P2｜结果与模拟发布的关键决策信息散落，发布阻碍没有直接解决路径。**

    目标用户：评估是否值得开始 paper evaluation 的研究员。触发场景：完成运行，比较独立样本、成本差异，并遇到 existing inventory 或 halted blocker。

    观察：result 的配置范围和 OOS 标签已经较明确；但成本、provenance、folds/scenarios 位于各自 tabs，release 入口在当前详情表后；Next research decision 是通用说明。release blocker 只渲染文本，没有导航到具体持仓、订单、控制器或风险状态。[ProResearch.tsx:949](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L949)、[ProResearch.tsx:1160](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/ProResearch.tsx#L1160)、[ResearchRelease.tsx:190](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/ResearchRelease.tsx#L190)。

    改进：结果先组织成“评估的是什么、证据范围、表现与成本、还有什么未解决、下一步选择”。这是重新排列已有数据。发布 blocker 应有具体对象与“检查/处理”入口，处理后返回同一 run/selection review 并重新 preview；保留 preview hash、acknowledgements、批准与激活分离、启动前 admission recheck。

13. **P2｜Next actions 缺少对象身份，且研究故障与交易风险的视觉强度混在一起。**

    目标用户：每天开盘快速判断优先工作的人。触发场景：存在多条 completed runs 或 blocked research packages。

    观察：[overview desktop](../assets/desk-overview-v0.13-desktop.png) 中两项都叫 Review a research result；主要区别藏在 Context & task ID。源码确实使用通用标题，hash 也放在折叠内容中。[WorkspaceJourney.tsx:285](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/WorkspaceJourney.tsx#L285)、[WorkspaceJourney.tsx:390](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/components/WorkspaceJourney.tsx#L390)。blocked package 是 warning，但 priority banner 的样式固定使用负面红色；ready 又要求研究 API 与 trading API 同时成功。[Overview.tsx:232](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Overview.tsx#L232)、[Overview.tsx:287](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/pages/Overview.tsx#L287)、[desk.css:58](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/desk.css#L58)。

    改进：动作名称带对象：项目 v3 · BTC-USDT · walk-forward 已完成，副行显示窗口与更新时间。Trading risk、支持数据故障和 research queue 使用各自的状态语义；红色只用于当前应处理的执行/估值风险，研究数据阻塞保留明确 warning。

14. **P2｜视觉系统有品牌辨识，但金融数据扫描层次仍弱。**

    目标用户：长时间使用桌面的交易员/研究员。触发场景：阅读密集账簿和跨区块判断状态。

    观察：侧栏 neon active、关键 CTA、大片 pale green 区域与大量 green muted 文本共用色系；timestamp、单位、说明、操作按钮与数据之间层级接近。dense table 主要值 12px、secondary 11px；数量和 mark 每行重复单位/时间造成约六十像素的行高，却仍需要横向滚动。[styles.css:15](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/styles.css#L15)、[table-desk.css:67](https://github.com/billpwchan/tidebench/blob/2b66f9c0e43fb901888424b8922293624a17b077/frontend/src/table-desk.css#L67)、[desk 桌面截图](../assets/desk-execution-v0.13-desktop.png)。

    美感判断属于审计推断：现界面更接近整洁的管理后台，缺少稳定、紧凑、可快速比较的金融工作表秩序；问题不是必须换成黑色主题。

    改进：用少量字体级别与间距 token 建立清晰层次；数值采用 tabular alignment、统一精度和右对齐；单位提升到列标题，重复时间在必要时展开；状态颜色具有固定含义，来源/更新时间保持可见。桌面提供适度紧凑密度，移动端保证可触达的行操作。品牌绿色保留，但降低导航与背景的持续视觉竞争。

完整设计方向应围绕三个连续工作表面，而不是继续堆叠模块。

| 表面 | 第一优先任务 | 主体布局 | 连续性要求 |
|---|---|---|---|
| 交易台 | 当前风险→持仓/订单→操作→结果 | 紧凑账户/时效行；异常；稳定账簿；独立 market inspector；独立 ticket/receipt | inspect 不改草稿；结果精确定位；关闭回到来源；已填写下一笔不被异步回执覆盖 |
| 研究台 | 假设/版本→输入→试验→证据→决策→paper | 项目/版本身份；trial ledger；固定结果 inspector；下一次试验草稿；release review | 研究对象一直可识别；选择运行不替换草稿；支持页带返回对象；保留 frozen inputs 与 evidence scope |
| Data / Operations | 解决当前任务所缺的输入或运行条件 | 面向具体 package/job/incident 的列表与详情 | 显示“从哪个研究/部署来”；完成后返回；重新核验当前状态 |

成熟产品资料只作为设计推理参照：TradingView 的 account manager 把 active positions、orders、订单 history 和 trading journal 放在同一账户语境；QuantConnect 以项目下的 backtest results 呈现曲线、统计和订单事件。这支持“稳定对象上下文与可追踪结果”的方向，不能作为本平台需要照搬功能清单的理由。[TradingView account manager](https://www.tradingview.com/support/solutions/43000786138-what-is-the-account-manager/)、[QuantConnect backtesting results](https://www.quantconnect.com/docs/v2/cloud-platform/backtesting/results)。

建议按以下批次迭代，每批先解决任务闭环，再处理视觉细节：

| 批次 | 实施重点 | 可验收的结果 |
|---|---|---|
| A：交易闭环 | 订单/撤单回执、精确定位；market inspector 与 ticket 分离；减仓影响清单 | 提交后无需猜测便能找到唯一订单与 outcome；连续草稿保留；inspect 市场不改变订单；停组影响可核对 |
| B：账簿与移动巡视 | 必要风险列、紧凑操作列、密度；移动首屏与简洁列表；减少嵌套滚动 | 1440px ticket 打开时必要风险字段同屏；手机首次打开即可看到至少首条持仓/订单；12 条持仓可连续扫描 |
| C：研究连续性 | 项目/版本身份、固定 trial ledger、兼容数据选择、结果决策顺序、blocker 导航 | 找到某版本的试验不用逐个开卡片；比较时保留选择；解决 blocker 后返回同一 review；scope 与 costs 可直接核对 |
| D：视觉系统 | 字体、spacing、数值、状态、按钮与 surfaces 的统一 tokens | 相同信息使用相同呈现；紧凑模式仍可读；桌面/移动信息优先级一致；状态强度与真实严重度相符 |

后续实测应集中在这些尚未验证的任务，而非重复截图验收：网络慢或失败时回执能否仍归属正确订单；300 条订单内定位结果；浏览器后退与刷新后的草稿、筛选、焦点恢复；减仓 prepare 部分失败时的影响清单；mobile ticket/内部滚动的实际触摸行为；多版本、多数据覆盖区间、80+ trials 的查找与比较。

已有值得保留的约束包括：synthetic 与 OKX 来源隔离、明确 local paper、未知值不编造、不可变版本与输入、训练/独立测试范围说明、成本差异确认、发布批准与激活分离，以及 protective exit 的库存与控制器重新核验。下一版本应让这些真实性约束更容易理解和操作，不应制造收益、活跃状态或实盘能力的错觉。
