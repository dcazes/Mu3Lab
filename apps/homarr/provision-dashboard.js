#!/usr/bin/env node

/**
 * Provision the curated Mu3Lab Homarr board after the first OIDC user exists.
 *
 * This intentionally talks to Homarr's local SQLite database instead of
 * creating a permanent API key. It is idempotent: an existing curated board
 * is left untouched unless --replace is supplied.
 */

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const Database = require("better-sqlite3");

const databasePath = process.env.HOMARR_DATABASE_PATH || "/appdata/db/db.sqlite";
const replace = process.argv.includes("--replace");
const boardName = "mu3lab";
const marker = "Mu3Lab Command Center";

const homarrBase = new URL(
  process.env.HOMARR_BASE_URL || process.env.BASE_URL || "https://mu3lab.local:8458",
);
const dashboardBase = `${homarrBase.protocol}//${homarrBase.hostname}:8446`;
const iconBase = "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg";
const controlToken = process.env.HOMARR_CONTROL_TOKEN || "";
const encryptionKey = process.env.HOMARR_SECRET_ENCRYPTION_KEY || process.env.SECRET_ENCRYPTION_KEY || "";
const json = (value) => JSON.stringify({ json: value });
const id = () => crypto.randomBytes(18).toString("base64url");
const encryptSecret = (value) => {
  if (!/^[0-9a-f]{64}$/i.test(encryptionKey)) {
    throw new Error("HOMARR_SECRET_ENCRYPTION_KEY must be the active 32-byte hex key.");
  }
  const iv = crypto.randomBytes(16);
  const cipher = crypto.createCipheriv("aes-256-cbc", Buffer.from(encryptionKey, "hex"), iv);
  const encrypted = Buffer.concat([cipher.update(value), cipher.final()]);
  return `${encrypted.toString("hex")}.${iv.toString("hex")}`;
};

const db = new Database(databasePath);
db.pragma("foreign_keys = ON");

const user = db.prepare(`
  SELECT id, name FROM user
  WHERE provider = 'oidc'
  ORDER BY email_verified DESC, rowid ASC
  LIMIT 1
`).get();

if (!user) {
  throw new Error("No initialized OIDC user exists in Homarr yet.");
}

const existing = db.prepare("SELECT id, meta_title FROM board WHERE name = ?").get(boardName);
if (existing && !replace) {
  console.log(JSON.stringify({ changed: false, boardId: existing.id, reason: "already-provisioned" }));
  process.exit(0);
}
if (existing && existing.meta_title !== marker) {
  throw new Error(`Refusing to replace non-Mu3Lab board named ${boardName}.`);
}
const existingLayout = existing && db.prepare("SELECT id FROM layout WHERE board_id = ? ORDER BY breakpoint LIMIT 1").get(existing.id);
const existingSections = existing && db.prepare("SELECT id, name, kind FROM section WHERE board_id = ? ORDER BY y_offset, x_offset").all(existing.id) || [];
const sectionDefinitions = [
  { key: "overview", kind: "category", name: "Platform overview", x: 0, y: 0 },
  { key: "overviewItems", kind: "empty", name: null, x: 0, y: 1 },
  { key: "platform", kind: "category", name: "Quick access", x: 0, y: 6 },
  { key: "platformItems", kind: "empty", name: null, x: 0, y: 7 },
  { key: "status", kind: "category", name: "Application status", x: 0, y: 10 },
  { key: "statusItems", kind: "empty", name: null, x: 0, y: 11 },
  { key: "controls", kind: "category", name: "Application controls", x: 0, y: 19 },
  { key: "controlItems", kind: "empty", name: null, x: 0, y: 20 },
  { key: "infrastructure", kind: "category", name: "Infrastructure", x: 0, y: 28 },
  { key: "infrastructureItems", kind: "empty", name: null, x: 0, y: 29 },
  { key: "ai", kind: "category", name: "AI & research", x: 0, y: 32 },
  { key: "aiItems", kind: "empty", name: null, x: 0, y: 33 },
  { key: "productivity", kind: "category", name: "Work & life", x: 0, y: 38 },
  { key: "productivityItems", kind: "empty", name: null, x: 0, y: 39 },
  { key: "operations", kind: "category", name: "Container inspector", x: 0, y: 44 },
  { key: "operationsItems", kind: "empty", name: null, x: 0, y: 45 },
];

const backupPath = path.join(
  path.dirname(databasePath),
  `db.sqlite.pre-dashboard-${new Date().toISOString().replace(/[-:]/g, "").replace(/\..+/, "")}`,
);
if (fs.existsSync(backupPath)) {
  throw new Error(`Backup path already exists: ${backupPath}`);
}
db.prepare(`VACUUM INTO ?`).run(backupPath);

const apps = [
  { key: "home", group: "platform", name: "Mu3Lab Home", description: "Primary platform dashboard", icon: "homarr", href: `${dashboardBase}/` },
  { key: "catalog", group: "platform", name: "App Catalog", description: "Install, configure, and inspect applications", icon: "pvy-appstore", href: `${dashboardBase}/apps` },
  { key: "chat", group: "platform", name: "AI Chat", description: "Open the private AI workspace", icon: "open-webui", href: `${dashboardBase}/chat` },
  { key: "connections", group: "platform", name: "Connections", description: "Providers, calendars, SSO, and MCP connections", icon: "authentik", href: `${dashboardBase}/connections` },
  { key: "security", group: "platform", name: "Protection", description: "Identity, audit, and backup readiness", icon: "vaultwarden-light", href: `${dashboardBase}/security` },
  { key: "system", group: "platform", name: "System", description: "Host status, jobs, and diagnostics", icon: "grafana", href: `${dashboardBase}/system` },
  { key: "homarr", group: "platform", name: "Manage Homarr", description: "Boards, apps, widgets, and integrations", icon: "homarr", href: `${homarrBase.origin}/manage` },
  { key: "authentik", group: "infrastructure", name: "Authentik", description: "Identity and access administration", icon: "authentik", href: `${homarrBase.protocol}//${homarrBase.hostname}` },
  { key: "vaultwarden", group: "infrastructure", name: "Vaultwarden", description: "Private password manager", icon: "vaultwarden-light", href: `${homarrBase.protocol}//${homarrBase.hostname}:8443` },
  { key: "open-webui", group: "ai", name: "Open WebUI", description: "Private model chat workspace", icon: "open-webui", href: `${dashboardBase}/chat` },
  { key: "litellm", group: "ai", name: "LiteLLM", description: "Model gateway and routing dashboard", iconUrl: "https://cdn.jsdelivr.net/gh/selfhst/icons/svg/litellm-light.svg", href: `${homarrBase.protocol}//${homarrBase.hostname}:8454/ui/` },
  { key: "freellmapi", group: "ai", name: "FreeLLMAPI", description: "Provider routing and account recovery", icon: "openai-light", href: `${homarrBase.protocol}//${homarrBase.hostname}:8455` },
  { key: "lobechat", group: "ai", name: "LobeChat", description: "Private AI workspace", icon: "lobe-chat", href: `${homarrBase.protocol}//${homarrBase.hostname}:8457` },
  { key: "surfsense", group: "ai", name: "SurfSense", description: "Private research and knowledge workspace", icon: "searxng", href: `${homarrBase.protocol}//${homarrBase.hostname}:8447` },
  { key: "firecrawl", group: "ai", name: "Firecrawl", description: "Private web extraction service", iconUrl: "https://cdn.jsdelivr.net/gh/selfhst/icons/svg/firecrawl.svg", href: `${homarrBase.protocol}//${homarrBase.hostname}:8456` },
  { key: "actual-budget", group: "productivity", name: "Actual Budget", description: "Private personal finance", icon: "actual-budget", href: `${homarrBase.protocol}//${homarrBase.hostname}:8448` },
  { key: "immich", group: "productivity", name: "Immich", description: "Private photo and video library", icon: "immich", href: `${homarrBase.protocol}//${homarrBase.hostname}:8449` },
  { key: "mealie", group: "productivity", name: "Mealie", description: "Recipes and meal planning", icon: "mealie", href: `${homarrBase.protocol}//${homarrBase.hostname}:8450` },
  { key: "adventurelog", group: "productivity", name: "AdventureLog", description: "Travel planning and memories", iconUrl: "https://cdn.jsdelivr.net/gh/selfhst/icons/svg/adventurelog.svg", href: `${homarrBase.protocol}//${homarrBase.hostname}:8451` },
  { key: "paperless-ngx", group: "productivity", name: "Paperless-ngx", description: "Document archive and search", icon: "paperless-ngx", href: `${homarrBase.protocol}//${homarrBase.hostname}:8452` },
  { key: "nextcloud", group: "productivity", name: "Nextcloud", description: "Files, collaboration, and productivity", icon: "nextcloud", href: `${homarrBase.protocol}//${homarrBase.hostname}:8453` },
  { key: "calendar", group: "productivity", name: "Nextcloud Calendar", description: "Open the connected Mu3Lab calendar", icon: "nextcloud-calendar", href: `${homarrBase.protocol}//${homarrBase.hostname}:8453/apps/calendar/` },
];

const controllableApps = [
  ["ollama", "Ollama", "ollama"],
  ["litellm", "LiteLLM", "openai-light"],
  ["open-webui", "Open WebUI", "open-webui"],
  ["freellmapi", "FreeLLMAPI", "openai-light"],
  ["surfsense", "SurfSense", "searxng"],
  ["firecrawl", "Firecrawl", "firefox"],
  ["lobehub", "LobeChat", "lobe-chat"],
  ["actual-budget", "Actual Budget", "actual-budget"],
  ["immich", "Immich", "immich"],
  ["mealie", "Mealie", "mealie"],
  ["adventurelog", "AdventureLog", "maptiler"],
  ["paperless-ngx", "Paperless-ngx", "paperless-ngx"],
  ["nextcloud", "Nextcloud", "nextcloud"],
].map(([key, name, icon]) => ({ key, name, icon }));

const pulseTemplate = `<Stack gap="md" p="xs">
  <Card withBorder radius="lg" p="lg" shadow="sm" style={{ background: "linear-gradient(135deg, rgba(91,33,182,0.32), rgba(8,145,178,0.18))", border: "1px solid rgba(103,232,249,0.24)" }}>
    <Group justify="space-between" align="flex-start">
      <Stack gap={3}>
        <Text size="xs" fw={800} tt="uppercase" c="cyan">Private application platform</Text>
        <Title order={2}>Mu3Lab Command Center</Title>
        <Text size="sm" c="dimmed">${homarrBase.hostname} · {String(data.OperatingSystem)}</Text>
      </Stack>
      <Badge size="lg" color="teal" variant="light">Live</Badge>
    </Group>
  </Card>
  <SimpleGrid cols={4} spacing="sm">
    <Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed">Running</Text><Text size="xl" fw={800}>{Number(data.ContainersRunning)}</Text></Card>
    <Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed">Stopped</Text><Text size="xl" fw={800}>{Number(data.ContainersStopped)}</Text></Card>
    <Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed">Images</Text><Text size="xl" fw={800}>{Number(data.Images)}</Text></Card>
    <Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed">CPU cores</Text><Text size="xl" fw={800}>{Number(data.NCPU)}</Text></Card>
  </SimpleGrid>
  <Group gap="xs">
    <Anchor href="${dashboardBase}/apps" target="_blank" underline="never"><Badge size="lg" variant="light" color="violet">App catalog</Badge></Anchor>
    <Anchor href="${dashboardBase}/system" target="_blank" underline="never"><Badge size="lg" variant="light" color="cyan">System</Badge></Anchor>
    <Anchor href="${dashboardBase}/connections" target="_blank" underline="never"><Badge size="lg" variant="light" color="indigo">Connections</Badge></Anchor>
    <Anchor href="${dashboardBase}/security" target="_blank" underline="never"><Badge size="lg" variant="light" color="teal">Protection</Badge></Anchor>
  </Group>
</Stack>`;

const statusTemplate = `<Stack gap="md" p="xs">
  <SimpleGrid cols={4} spacing="sm">
    <Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed" tt="uppercase" fw={700}>Running</Text><Text size="xl" fw={800} c="teal">{Number(data.summary.running)}</Text></Card>
    <Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed" tt="uppercase" fw={700}>Stopped</Text><Text size="xl" fw={800}>{Number(data.summary.stopped)}</Text></Card>
    <Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed" tt="uppercase" fw={700}>Attention</Text><Text size="xl" fw={800} c="yellow">{Number(data.summary.attention)}</Text></Card>
    <Card withBorder radius="lg" p="md"><Text size="xs" c="dimmed" tt="uppercase" fw={700}>Registered</Text><Text size="xl" fw={800} c="violet">{Number(data.summary.total)}</Text></Card>
  </SimpleGrid>
  <SimpleGrid cols={3} spacing="xs">
    {data.services.map((service) =>
      <Card withBorder radius="md" p="sm">
        <Group justify="space-between" wrap="nowrap">
          <Stack gap={1}>
            <Text size="sm" fw={700}>{service.name}</Text>
            <Text size="xs" c="dimmed">{service.category}</Text>
          </Stack>
          <Badge size="sm" color={service.tone} variant="light">{service.stateLabel}</Badge>
        </Group>
      </Card>
    )}
  </SimpleGrid>
</Stack>`;

const run = db.transaction(() => {
  // Keep the board identity intact when refreshing its seeded contents. Homarr
  // editors hold the board ID in memory, so deleting/recreating the row makes
  // their later saveBoard request fail with "Board not found".
  const boardId = existing?.id || id();
  const layoutId = existingLayout?.id || id();
  const sectionIds = {};
  const remainingSections = [...existingSections];
  for (const section of sectionDefinitions) {
    const chosenIndex = remainingSections.findIndex((candidate) =>
      candidate.name === section.name && candidate.kind === section.kind,
    );
    sectionIds[section.key] = chosenIndex >= 0 ? remainingSections.splice(chosenIndex, 1)[0].id : id();
  }

  if (existing) {
    db.prepare("DELETE FROM item WHERE board_id = ?").run(boardId);
    db.prepare("DELETE FROM layout WHERE board_id = ? AND id <> ?").run(boardId, layoutId);
    db.prepare(`
      UPDATE board SET name = ?, is_public = 0, creator_id = ?, page_title = ?, meta_title = ?,
        primary_color = '#7c3aed', secondary_color = '#06b6d4', opacity = 96,
        disable_status = 0, item_radius = 'lg'
      WHERE id = ?
    `).run(boardName, user.id, marker, marker, boardId);
    if (existingLayout) {
      db.prepare("UPDATE layout SET name = 'Desktop', column_count = 12, breakpoint = 0 WHERE id = ?").run(layoutId);
    } else {
      db.prepare("INSERT INTO layout (id, name, board_id, column_count, breakpoint) VALUES (?, 'Desktop', ?, 12, 0)").run(layoutId, boardId);
    }
  } else {
    db.prepare(`
      INSERT INTO board (
        id, name, is_public, creator_id, page_title, meta_title,
        primary_color, secondary_color, opacity, disable_status, item_radius
      ) VALUES (?, ?, 0, ?, ?, ?, '#7c3aed', '#06b6d4', 96, 0, 'lg')
    `).run(boardId, boardName, user.id, marker, marker);
    db.prepare("INSERT INTO layout (id, name, board_id, column_count, breakpoint) VALUES (?, 'Desktop', ?, 12, 0)").run(layoutId, boardId);
  }

  const upsertSection = db.prepare(`
    INSERT INTO section (id, board_id, kind, x_offset, y_offset, name, options)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      kind = excluded.kind, x_offset = excluded.x_offset, y_offset = excluded.y_offset,
      name = excluded.name, options = excluded.options
  `);
  for (const section of sectionDefinitions) {
    upsertSection.run(sectionIds[section.key], boardId, section.kind, section.x, section.y, section.name, json({}));
  }
  const retainedSectionIds = Object.values(sectionIds);
  if (remainingSections.length) {
    const placeholders = retainedSectionIds.map(() => "?").join(", ");
    db.prepare(`DELETE FROM section WHERE board_id = ? AND id NOT IN (${placeholders})`)
      .run(boardId, ...retainedSectionIds);
  }

  const upsertApp = db.prepare(`
    INSERT INTO app (id, name, description, icon_url, href, ping_url)
    VALUES (?, ?, ?, ?, ?, ?)
    ON CONFLICT(id) DO UPDATE SET
      name = excluded.name,
      description = excluded.description,
      icon_url = excluded.icon_url,
      href = excluded.href,
      ping_url = excluded.ping_url
  `);
  for (const app of apps) {
    app.id = `mu3lab-${app.key}`;
    upsertApp.run(app.id, app.name, app.description, app.iconUrl || `${iconBase}/${app.icon}.svg`, app.href, app.href);
  }

  const upsertCustomWidget = db.prepare(`
    INSERT INTO custom_widget_definition (
      id, name, description, icon_url, url, auth_type, method,
      request_body, display_type, display_config, enabled, creator_id
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
    ON CONFLICT(id) DO UPDATE SET
      name = excluded.name,
      description = excluded.description,
      icon_url = excluded.icon_url,
      url = excluded.url,
      auth_type = excluded.auth_type,
      method = excluded.method,
      request_body = excluded.request_body,
      display_type = excluded.display_type,
      display_config = excluded.display_config,
      enabled = 1,
      updated_at = unixepoch(),
      creator_id = excluded.creator_id
  `);
  upsertCustomWidget.run(
    "mu3lab-platform-pulse",
    "Mu3Lab Platform Pulse",
    "Live, read-only Docker platform summary and control-plane shortcuts",
    `${iconBase}/homarr.svg`,
    "http://socket-proxy:2375/info",
    "none",
    "GET",
    null,
    "customJsx",
    json({ type: "customJsx", template: pulseTemplate }),
    user.id,
  );

  if (!controlToken) {
    throw new Error("HOMARR_CONTROL_TOKEN is required for native lifecycle widgets.");
  }
  upsertCustomWidget.run(
    "mu3lab-application-status",
    "Mu3Lab Application Status",
    "Live state of every curated Mu3Lab service",
    `${iconBase}/docker.svg`,
    `${dashboardBase}/api/v1/homarr/services`,
    "bearer",
    "GET",
    null,
    "customJsx",
    json({ type: "customJsx", template: statusTemplate }),
    user.id,
  );
  for (const app of controllableApps) {
    upsertCustomWidget.run(
      `mu3lab-control-${app.key}`,
      app.name,
      `Start or stop the complete ${app.name} application stack`,
      `${iconBase}/${app.icon}.svg`,
      `${dashboardBase}/api/v1/homarr/services/${app.key}/toggle`,
      "bearer",
      "POST",
      "{}",
      "actionButton",
      json({
        type: "actionButton",
        buttonLabel: "Start / Stop",
        buttonColor: "violet",
        confirmText: `Start ${app.name} if it is stopped, or stop it if it is running?`,
        successMessage: `${app.name} lifecycle action queued.`,
      }),
      user.id,
    );
  }

  const retainedDefinitions = [
    "mu3lab-platform-pulse",
    "mu3lab-application-status",
    ...controllableApps.map((app) => `mu3lab-control-${app.key}`),
  ];
  const definitionPlaceholders = retainedDefinitions.map(() => "?").join(", ");
  db.prepare(`DELETE FROM custom_widget_definition WHERE id LIKE 'mu3lab-%' AND id NOT IN (${definitionPlaceholders})`)
    .run(...retainedDefinitions);
  const upsertSecret = db.prepare(`
    INSERT INTO custom_widget_secret (kind, value, updated_at, definition_id)
    VALUES ('apiKey', ?, unixepoch(), ?)
    ON CONFLICT(definition_id, kind) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
  `);
  const encryptedControlToken = encryptSecret(controlToken);
  for (const definitionId of retainedDefinitions.filter((value) => value !== "mu3lab-platform-pulse")) {
    upsertSecret.run(encryptedControlToken, definitionId);
  }

  const insertItem = db.prepare("INSERT INTO item (id, board_id, kind, options, advanced_options) VALUES (?, ?, ?, ?, ?)");
  const insertLayout = db.prepare("INSERT INTO item_layout (item_id, section_id, layout_id, x_offset, y_offset, width, height) VALUES (?, ?, ?, ?, ?, ?, ?)");
  const addItem = (key, kind, options, sectionKey, x, y, width, height, title = null) => {
    const itemId = `mu3lab-item-${key}`;
    insertItem.run(itemId, boardId, kind, json(options), json({ title, customCssClasses: [], borderColor: "" }));
    insertLayout.run(itemId, sectionIds[sectionKey], layoutId, x, y, width, height);
  };

  addItem("pulse", "customApi", { definitionId: "mu3lab-platform-pulse", refreshInterval: 30 }, "overviewItems", 0, 0, 8, 4);
  addItem("clock", "clock", {
    customTitleToggle: true,
    customTitle: "Toronto",
    is24HourFormat: false,
    showSeconds: false,
    useCustomTimezone: true,
    timezone: "America/Toronto",
    showDate: true,
    dateFormat: "dddd, MMMM D",
    customTimeFormat: "",
    customDateFormat: "",
    showWeather: false,
    weatherLocation: { name: "Toronto", latitude: 43.6532, longitude: -79.3832 },
    isWeatherFormatFahrenheit: false,
  }, "overviewItems", 8, 0, 4, 4);

  addItem("application-status", "customApi", {
    definitionId: "mu3lab-application-status",
    refreshInterval: 15,
  }, "statusItems", 0, 0, 12, 12, "All applications");

  for (const [index, app] of controllableApps.entries()) {
    addItem(`control-${app.key}`, "customApi", {
      definitionId: `mu3lab-control-${app.key}`,
      refreshInterval: 0,
    }, "controlItems", (index % 4) * 3, Math.floor(index / 4) * 2, 3, 2, app.name);
  }

  const groupCounts = {};
  for (const app of apps) {
    const index = groupCounts[app.group] || 0;
    groupCounts[app.group] = index + 1;
    const x = (index % 6) * 2;
    const y = Math.floor(index / 6) * 2;
    const sectionKey = app.group === "platform" ? "platformItems"
      : app.group === "infrastructure" ? "infrastructureItems"
        : app.group === "ai" ? "aiItems" : "productivityItems";
    addItem(`app-${app.key}`, "app", {
      appId: app.id,
      openInNewTab: true,
      showTitle: true,
      descriptionDisplayMode: "tooltip",
      layout: "column",
      pingEnabled: true,
    }, sectionKey, x, y, 2, 2);
  }

  addItem("docker", "dockerContainers", {
    columns: ["name", "state", "host", "cpuUsage", "memoryUsage", "actions"],
    enableRowSorting: true,
    defaultSort: "state",
    descendingDefaultSort: true,
    columnOrder: "",
    columnWidths: "",
  }, "operationsItems", 0, 0, 12, 5, "Workload Inspector");

  db.prepare("UPDATE user SET home_board_id = ?, mobile_home_board_id = ? WHERE id = ?").run(boardId, boardId, user.id);
  db.prepare("UPDATE `group` SET home_board_id = ?, mobile_home_board_id = ? WHERE name IN ('authentik Admins', 'mu3lab-operators')").run(boardId, boardId);

  return { boardId, layoutId, sectionIds };
});

const result = run();
db.pragma("wal_checkpoint(TRUNCATE)");

const verification = {
  board: db.prepare("SELECT id, name, page_title, meta_title, primary_color, secondary_color FROM board WHERE id = ?").get(result.boardId),
  itemCount: db.prepare("SELECT count(*) AS count FROM item WHERE board_id = ?").get(result.boardId).count,
  appCount: db.prepare("SELECT count(*) AS count FROM app WHERE id LIKE 'mu3lab-%'").get().count,
  customWidgetCount: db.prepare("SELECT count(*) AS count FROM custom_widget_definition WHERE id LIKE 'mu3lab-%' AND enabled = 1").get().count,
  iframeCount: db.prepare("SELECT count(*) AS count FROM item WHERE board_id = ? AND kind = 'iframe'").get(result.boardId).count,
  actionCount: db.prepare("SELECT count(*) AS count FROM item WHERE board_id = ? AND id LIKE 'mu3lab-item-control-%'").get(result.boardId).count,
  backupPath,
  backupBytes: fs.statSync(backupPath).size,
};

console.log(JSON.stringify({ changed: true, ...verification }, null, 2));
