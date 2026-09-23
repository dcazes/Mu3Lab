#!/usr/bin/env node

/** Idempotently replaces split app/status widgets with native Homarr v2 control tiles. */
const crypto = require("node:crypto");
const Database = require("better-sqlite3");

const db = new Database(process.env.HOMARR_DATABASE_PATH || "/appdata/db/db.sqlite");
db.pragma("foreign_keys = ON");
const encryptionKey = process.env.SECRET_ENCRYPTION_KEY || process.env.HOMARR_SECRET_ENCRYPTION_KEY || "";
const controlToken = process.env.HOMARR_CONTROL_TOKEN || "";
if (!/^[0-9a-f]{64}$/i.test(encryptionKey)) throw new Error("Homarr's 32-byte SECRET_ENCRYPTION_KEY is required.");
if (!controlToken) throw new Error("HOMARR_CONTROL_TOKEN is required.");

const base = new URL(process.env.HOMARR_BASE_URL || process.env.BASE_URL);
const apiBase = process.env.HOMARR_CONTROL_API_BASE || "http://172.21.0.1:19460";
const dashboardBase = `${base.protocol}//${base.hostname}:8446`;
const icons = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg";
const apps = [
  { id: "ollama", name: "Ollama", icon: "ollama", href: "" },
  { id: "litellm", name: "LiteLLM", icon: "openai-light", href: `${base.protocol}//${base.hostname}:8454/ui/` },
  { id: "open-webui", name: "Open WebUI", icon: "open-webui", href: `${base.protocol}//${base.hostname}:8446/chat` },
  { id: "freellmapi", name: "FreeLLMAPI", icon: "openai-light", href: `${base.protocol}//${base.hostname}:8455` },
  { id: "surfsense", name: "SurfSense", icon: "searxng", href: `${base.protocol}//${base.hostname}:8447` },
  { id: "firecrawl", name: "Firecrawl", icon: "firecrawl", href: `${base.protocol}//${base.hostname}:8456` },
  { id: "lobehub", name: "LobeChat", icon: "lobe-chat", href: `${base.protocol}//${base.hostname}:8457` },
  { id: "actual-budget", name: "Actual Budget", icon: "actual-budget", href: `${base.protocol}//${base.hostname}:8448` },
  { id: "immich", name: "Immich", icon: "immich", href: `${base.protocol}//${base.hostname}:8449` },
  { id: "mealie", name: "Mealie", icon: "mealie", href: `${base.protocol}//${base.hostname}:8450` },
  { id: "adventurelog", name: "AdventureLog", icon: "adventurelog", href: `${base.protocol}//${base.hostname}:8451` },
  { id: "paperless-ngx", name: "Paperless-ngx", icon: "paperless-ngx", href: `${base.protocol}//${base.hostname}:8452` },
  { id: "nextcloud", name: "Nextcloud", icon: "nextcloud", href: `${base.protocol}//${base.hostname}:8453` },
];
const shortcutIds = {
  "open-webui": "open-webui", lobehub: "lobechat",
};
const groups = new Map([
  ["ollama", "aiItems"], ["litellm", "aiItems"], ["open-webui", "aiItems"],
  ["freellmapi", "aiItems"], ["surfsense", "aiItems"], ["firecrawl", "aiItems"],
  ["lobehub", "aiItems"], ["actual-budget", "productivityItems"], ["immich", "productivityItems"],
  ["mealie", "productivityItems"], ["adventurelog", "productivityItems"],
  ["paperless-ngx", "productivityItems"], ["nextcloud", "productivityItems"],
]);
const encrypted = (value) => {
  const iv = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv("aes-256-cbc", Buffer.from(encryptionKey, "hex"), iv);
  return `${Buffer.concat([cipher.update(value), cipher.final()]).toString("hex")}.${iv.toString("hex")}`;
};
const packed = (value) => JSON.stringify({ json: value });
const escaped = (value) => value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");
const user = db.prepare("SELECT id FROM user ORDER BY rowid LIMIT 1").get();
if (!user) throw new Error("Initialize the Homarr administrator before provisioning the board.");
const board = db.prepare("SELECT id FROM board WHERE name = 'mu3lab'").get();
if (!board) throw new Error("Expected Mu3Lab board was not found; refusing to create a parallel board.");

const run = db.transaction(() => {
  const alreadyCombined = db.prepare("SELECT count(*) AS count FROM custom_widget_v2_definition WHERE id LIKE 'mu3lab-v2-control-%' AND enabled=1").get().count === apps.length;
  const source = { default: { name: "Mu3Lab control plane", baseUrl: apiBase, networkScope: "private", auth: "bearer" } };
  const secret = encrypted(controlToken);
  const upsertDefinition = db.prepare(`
    INSERT INTO custom_widget_v2_definition
      (id, name, description, icon_url, sources, requests, options, template, enabled, creator_id)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    ON CONFLICT(id) DO UPDATE SET name=excluded.name, description=excluded.description,
      icon_url=excluded.icon_url, sources=excluded.sources, requests=excluded.requests,
      options=excluded.options, template=excluded.template, enabled=1, updated_at=unixepoch(), creator_id=excluded.creator_id
  `);
  const upsertSecret = db.prepare(`
    INSERT INTO custom_widget_v2_secret (source_id, kind, encrypted_value, updated_at, definition_id)
    VALUES ('default', 'apiKey', ?, unixepoch(), ?)
    ON CONFLICT(definition_id, kind, source_id) DO UPDATE SET encrypted_value=excluded.encrypted_value, updated_at=unixepoch()
  `);
  const pulseDefinition = {
    id: "mu3lab-v2-platform-pulse",
    name: "Mu3Lab Platform Pulse",
    description: "Live Docker capacity and shortcuts into the Mu3Lab control plane.",
    iconUrl: `${icons}/homarr.svg`,
    sources: { default: { name: "Read-only Docker socket proxy", baseUrl: "http://socket-proxy:2375", networkScope: "private", auth: "none" } },
    requests: { info: { source: "default", kind: "query", method: "GET", path: "/info", cacheSeconds: 30, trigger: "load", permission: "view" } },
    options: {},
    template: `<Stack gap="md" p="xs"><Card withBorder radius="lg" p="lg" shadow="sm" style={{background:"linear-gradient(135deg, rgba(91,33,182,0.32), rgba(8,145,178,0.18))",border:"1px solid rgba(103,232,249,0.24)"}}><Group justify="space-between" align="flex-start"><Stack gap={3}><Text size="xs" fw={800} tt="uppercase" c="cyan">Private application platform</Text><Title order={2}>Mu3Lab Command Center</Title><Text size="sm" c="dimmed">${base.hostname} · {String(data.info?.OperatingSystem ?? "Docker host")}</Text></Stack><Badge size="lg" color="teal" variant="light">Live</Badge></Group></Card><SimpleGrid cols={4} spacing="sm"><Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed">Running containers</Text><Text size="xl" fw={800} c="teal">{Number(data.info?.ContainersRunning ?? 0)}</Text></Card><Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed">Stopped containers</Text><Text size="xl" fw={800}>{Number(data.info?.ContainersStopped ?? 0)}</Text></Card><Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed">Images</Text><Text size="xl" fw={800}>{Number(data.info?.Images ?? 0)}</Text></Card><Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed">CPU cores</Text><Text size="xl" fw={800}>{Number(data.info?.NCPU ?? 0)}</Text></Card></SimpleGrid><Group gap="xs"><Anchor href="${dashboardBase}/apps" target="_blank" underline="never"><Badge size="lg" variant="light" color="violet">App catalog</Badge></Anchor><Anchor href="${dashboardBase}/system" target="_blank" underline="never"><Badge size="lg" variant="light" color="cyan">System</Badge></Anchor><Anchor href="${dashboardBase}/connections" target="_blank" underline="never"><Badge size="lg" variant="light" color="indigo">Connections</Badge></Anchor><Anchor href="${dashboardBase}/security" target="_blank" underline="never"><Badge size="lg" variant="light" color="teal">Protection</Badge></Anchor></Group></Stack>`,
  };
  const upsertV2Definition = db.prepare(`
    INSERT INTO custom_widget_v2_definition
      (id,name,description,icon_url,sources,requests,options,template,enabled,creator_id)
    VALUES (?,?,?,?,?,?,?, ?,1,?)
    ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,icon_url=excluded.icon_url,
      sources=excluded.sources,requests=excluded.requests,options=excluded.options,template=excluded.template,
      enabled=1,updated_at=unixepoch(),creator_id=excluded.creator_id
  `);
  upsertV2Definition.run(pulseDefinition.id,pulseDefinition.name,pulseDefinition.description,pulseDefinition.iconUrl,
    packed(pulseDefinition.sources),packed(pulseDefinition.requests),packed(pulseDefinition.options),pulseDefinition.template,user.id);
  const pulseItem = db.prepare("SELECT id,options FROM item WHERE board_id=? AND id='mu3lab-item-pulse'").get(board.id);
  if (pulseItem) db.prepare("UPDATE item SET options=? WHERE id=?").run(packed({definitionId:pulseDefinition.id,refreshInterval:30}),pulseItem.id);
  db.prepare("DELETE FROM custom_widget_secret WHERE definition_id='mu3lab-platform-pulse'").run();
  db.prepare("DELETE FROM custom_widget_definition WHERE id='mu3lab-platform-pulse'").run();
  const layouts = db.prepare("SELECT id,breakpoint FROM layout WHERE board_id = ? ORDER BY breakpoint").all(board.id);
  if (!layouts.length) throw new Error("Mu3Lab board has no layout.");
  const rootSection = db.prepare(`SELECT il.section_id FROM item_layout il JOIN item i ON i.id=il.item_id
    WHERE i.board_id=? LIMIT 1`).get(board.id)?.section_id;
  if (!rootSection) throw new Error("Root board section is missing.");
  const containerRows = db.prepare("SELECT id,options FROM section WHERE board_id=? AND kind='container'").all(board.id);
  const sectionByTitle = Object.fromEntries(containerRows.map((row) => {
    try { return [JSON.parse(row.options).json.title, row.id]; } catch { return ["", row.id]; }
  }));
  const workSection = sectionByTitle["Work & life"];
  const removeOld = db.prepare("DELETE FROM item WHERE board_id = ? AND id = ?");
  const getExisting = db.prepare(`SELECT il.x_offset,il.y_offset,il.section_id,il.layout_id FROM item i JOIN item_layout il ON il.item_id=i.id
    WHERE i.board_id=? AND i.id=?`);
  const putItem = db.prepare(`
    INSERT INTO item (id, board_id, kind, options, advanced_options)
    VALUES (?, ?, 'customApi', ?, ?)
    ON CONFLICT(id) DO UPDATE SET kind='customApi', options=excluded.options, advanced_options=excluded.advanced_options
  `);
  const putLayout = db.prepare(`
    INSERT INTO item_layout (item_id,section_id,layout_id,x_offset,y_offset,width,height)
    VALUES (?,?,?,?,?,?,?) ON CONFLICT(item_id,section_id,layout_id) DO UPDATE SET
      x_offset=excluded.x_offset,y_offset=excluded.y_offset,width=excluded.width,height=excluded.height
  `);

  const priorPositions = new Map();
  for (const app of apps) {
    const priorKey = shortcutIds[app.id] || app.id;
    const rows = getExisting.all(board.id, `mu3lab-item-app-${priorKey}`);
    const currentRows = rows.length ? rows : getExisting.all(board.id, `mu3lab-item-v2-control-${app.id}`);
    priorPositions.set(app.id, new Map(currentRows.map((row) => [row.layout_id, row])));
  }
  const shiftSection = db.prepare("UPDATE section_layout SET y_offset=y_offset+3 WHERE layout_id=? AND y_offset>=?");
  const shiftItems = db.prepare(`UPDATE item_layout SET y_offset=y_offset+3 WHERE layout_id=? AND section_id=? AND y_offset>=?`);
  for (const layout of layouts) {
    const heading = workSection && db.prepare("SELECT y_offset FROM section_layout WHERE section_id=? AND layout_id=?").get(workSection, layout.id);
    if (heading && !alreadyCombined) {
      shiftSection.run(layout.id, heading.y_offset);
      shiftItems.run(layout.id, rootSection, heading.y_offset + 1);
    }
  }

  const removeUtilitySections = db.prepare(`SELECT id FROM section WHERE board_id=? AND kind='container'
    AND json_extract(options,'$.json.title') IN ('Application status','Application controls')`).all(board.id);
  for (const section of removeUtilitySections) {
    db.prepare("DELETE FROM section_layout WHERE section_id=?").run(section.id);
    db.prepare("DELETE FROM section WHERE id=?").run(section.id);
  }

  for (const app of apps) {
    const definitionId = `mu3lab-v2-control-${app.id}`;
    const appUrl = escaped(app.href);
    const statusUrl = escaped(`${dashboardBase}/apps/${app.id}`);
    const imageUrl = escaped(`${icons}/${app.icon}.svg`);
    const destination = `data.status?.service?.healthState === "healthy" && ${Boolean(app.href)} ? "${appUrl}" : "${statusUrl}"`;
    const template = `<Card withBorder radius="lg" p="sm" h="100%"><Stack justify="space-between" align="center" h="100%" gap="sm"><Stack gap="xs" align="center" w="100%"><Anchor href={${destination}} target="_blank" underline="never" aria-label="Open ${app.name}"><Avatar src="${imageUrl}" size={48} radius="xl" alt="${app.name}" /></Anchor><Anchor href={${destination}} target="_blank" underline="never" style={{width:"100%",maxWidth:"100%"}}><Text fw={700} size="sm" ta="center" lineClamp={2} style={{whiteSpace:"normal",overflowWrap:"anywhere"}}>${app.name}</Text></Anchor><Badge size="sm" color={data.status?.service?.tone ?? "gray"} variant="light">{data.status?.service?.stateLabel ?? (status.status?.loading ? "Checking" : "Unavailable")}</Badge></Stack><Group justify="center" wrap="nowrap"><ActionButton requestId="toggle" label={data.status?.service?.actionLabel ?? "Unavailable"} color={data.status?.service?.availableAction === "stop" ? "red" : "teal"} disabled={!data.status?.service?.availableAction} confirmMessage={data.status?.service?.availableAction === "stop" ? "Stop this application?" : "Start this application?"} successMessage="Application action queued" /></Group></Stack></Card>`;
    const definition = {
      default: source.default,
    };
    const requests = {
      status: { source: "default", kind: "query", method: "GET", path: `/api/v1/homarr/services/${app.id}`, cacheSeconds: 15, trigger: "load", permission: "view" },
      toggle: { source: "default", kind: "action", method: "POST", path: `/api/v1/homarr/services/${app.id}/toggle`, body: {}, permission: "modify", invalidates: ["status"], confirmation: { title: `${app.name} power control`, message: `Start or stop ${app.name} according to its current state?`, destructive: true } },
    };
    upsertDefinition.run(definitionId, app.name, `Live status and one-click lifecycle control for ${app.name}.`, `${icons}/${app.icon}.svg`, packed(definition), packed(requests), packed({}), template, user.id);
    db.prepare("DELETE FROM custom_widget_v2_secret WHERE source_id='default' AND kind='bearer' AND definition_id=?").run(definitionId);
    upsertSecret.run(secret, definitionId);

    const priorKey = shortcutIds[app.id] || app.id;
    const priorId = `mu3lab-item-app-${priorKey}`;
    const oldPositions = priorPositions.get(app.id);
    const itemId = `mu3lab-item-v2-control-${app.id}`;
    if (oldPositions.size) removeOld.run(board.id, priorId);
    const category = groups.get(app.id) === "aiItems" ? "AI & research" : "Work & life";
    putItem.run(itemId, board.id, packed({ definitionId, refreshInterval: 15 }), packed({ json: { title: app.name, customCssClasses: [], borderColor: "" } }));
    for (const layout of layouts) {
      const prior = oldPositions.get(layout.id);
      let x = prior?.x_offset ?? 0;
      let y = prior?.y_offset ?? 38;
      if (!prior && groups.get(app.id) === "aiItems") {
        const previousAi = apps.filter((entry) => entry.id !== app.id && groups.get(entry.id) === "aiItems")
          .map((entry) => priorPositions.get(entry.id).get(layout.id)?.y_offset).filter(Number.isInteger);
        y = (previousAi.length ? Math.max(...previousAi) : (layout.breakpoint === 0 ? 36 : 70)) + 2;
      }
      if (category === "Work & life" && workSection && !alreadyCombined) y += 3;
      putLayout.run(itemId, prior?.section_id || rootSection, layout.id, x, y, 2, 2);
    }
  }

  // Remove the split status list and standalone action buttons; each app now
  // has exactly one live tile in its existing category.
  db.prepare("DELETE FROM item WHERE board_id = ? AND (id LIKE 'mu3lab-item-control-%' OR id = 'mu3lab-item-application-status')").run(board.id);
  db.prepare("DELETE FROM custom_widget_definition WHERE id LIKE 'mu3lab-control-%' OR id = 'mu3lab-application-status'").run();
});
run();
db.pragma("wal_checkpoint(TRUNCATE)");
const verification = {
  board: db.prepare("SELECT id,name FROM board WHERE id=?").get(board.id),
    combinedTiles: db.prepare("SELECT count(*) AS count FROM item WHERE board_id=? AND kind='customApi' AND options LIKE '%mu3lab-v2-control-%'").get(board.id).count,
  v2Definitions: db.prepare("SELECT count(*) AS count FROM custom_widget_v2_definition WHERE id LIKE 'mu3lab-v2-control-%' AND enabled=1").get().count,
  splitActions: db.prepare("SELECT count(*) AS count FROM item WHERE board_id=? AND id LIKE 'mu3lab-item-control-%'").get(board.id).count,
  statusLists: db.prepare("SELECT count(*) AS count FROM item WHERE board_id=? AND id='mu3lab-item-application-status'").get(board.id).count,
};
if (verification.combinedTiles !== apps.length || verification.v2Definitions !== apps.length || verification.splitActions || verification.statusLists) {
  throw new Error(`Post-provision verification failed: ${JSON.stringify(verification)}`);
}
console.log(JSON.stringify({ ok: true, ...verification }));
