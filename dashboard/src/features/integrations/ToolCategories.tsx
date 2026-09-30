import { ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { type McpCategory, type McpServer, putJsonApi } from '../../api';
import { Collapsible } from '../../components/Layout';
import { Badge } from '../../components/Status';
import { useAction } from '../../lib/useAction';

type Tool = McpServer['tools'][number];

/** "search_smart" → "Search smart"; the connector's own description goes underneath. */
const toolLabel = (id: string) => {
  const words = id.replace(/^immich_/, '').replace(/[_-]+/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
};

function ToolRow({
  server,
  tool,
  categoryOn,
  reload,
  readOnly,
}: {
  server: McpServer;
  tool: Tool;
  categoryOn: boolean;
  reload: () => void;
  readOnly: boolean;
}) {
  const { pending, run } = useAction();
  const changes = tool.risk === 'write';
  const toggle = async (on: boolean) => {
    const permission = on ? (changes ? 'needs_approval' : 'auto') : 'disabled';
    const saved = await run(
      tool.id,
      () =>
        putJsonApi(`/api/v1/mcp/servers/${server.id}/tools/${encodeURIComponent(tool.id)}/permission`, {
          permission,
        }),
      `${toolLabel(tool.id)} switched ${on ? 'on' : 'off'}`,
    );
    if (saved) reload();
  };
  return (
    <div className="row tool-row">
      <span className="row-text">
        <b>
          {toolLabel(tool.id)}
          {tool.core && <Badge tone="blue">Everyday</Badge>}
          {changes && <Badge tone="amber">Asks you first</Badge>}
          {tool.offered === false && <Badge>Not offered by this version</Badge>}
        </b>
        {tool.title && <small>{tool.title}</small>}
      </span>
      <label className="switch-inline">
        <span className="sr-only">{toolLabel(tool.id)}</span>
        <input
          type="checkbox"
          role="switch"
          checked={tool.enabled}
          disabled={readOnly || !categoryOn || pending === tool.id || tool.offered === false}
          onChange={(event) => void toggle(event.target.checked)}
        />
      </label>
    </div>
  );
}

function Category({
  server,
  category,
  tools,
  reload,
  readOnly,
}: {
  server: McpServer;
  category: McpCategory;
  tools: Tool[];
  reload: () => void;
  readOnly: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { pending, run } = useAction();
  const on = tools.filter((tool) => tool.enabled).length;
  const toggle = async (enabled: boolean) => {
    const saved = await run(
      'category',
      () => putJsonApi(`/api/v1/mcp/servers/${server.id}/categories/${category.id}`, { enabled }),
      `${category.title} switched ${enabled ? 'on' : 'off'}`,
    );
    if (saved) reload();
  };
  const bodyId = `tools-${server.id}-${category.id}`;
  return (
    <section className={`tool-category${category.enabled ? '' : ' is-off'}`} aria-label={category.title}>
      <div className="tool-category-head">
        <button
          type="button"
          className="tool-category-toggle"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={() => setOpen((value) => !value)}
        >
          <ChevronRight className={open ? 'is-open' : undefined} />
          <span className="row-text">
            <b>{category.title}</b>
            <small>{category.summary}</small>
          </span>
          <span className="tool-count">{category.enabled ? `${on} of ${tools.length} on` : 'Off'}</span>
        </button>
        <label className="switch-inline">
          <span className="sr-only">{category.title}</span>
          <input
            type="checkbox"
            role="switch"
            checked={category.enabled}
            disabled={readOnly || pending === 'category'}
            onChange={(event) => void toggle(event.target.checked)}
          />
        </label>
      </div>
      {open && (
        <div className="rows compact" id={bodyId}>
          {tools.map((tool) => (
            <ToolRow
              key={tool.id}
              server={server}
              tool={tool}
              categoryOn={category.enabled}
              reload={reload}
              readOnly={readOnly}
            />
          ))}
        </div>
      )}
    </section>
  );
}

/** A gateway connector's tools, grouped by category, each with its own switch. */
export function ToolCategories({
  server,
  reload,
  readOnly = false,
}: {
  server: McpServer;
  reload: () => void;
  readOnly?: boolean;
}) {
  const categories = server.categories || [];
  return (
    <div className="stack">
      <p className="muted">
        Chat always sees the everyday tools and can look up the rest from categories that are on. It knows about
        categories that are off and tells you when one is needed. Tools that change data start off and ask you before
        every use.
      </p>
      <div className="tool-categories">
        {categories.map((category) => (
          <Category
            key={category.id}
            server={server}
            category={category}
            tools={server.tools.filter((tool) => tool.category === category.id)}
            reload={reload}
            readOnly={readOnly}
          />
        ))}
      </div>
      {Boolean(server.blocked?.length) && (
        <Collapsible title={`Not available in chat (${server.blocked!.length})`}>
          <div className="rows compact">
            {server.blocked!.map((tool) => (
              <div className="row" key={tool.id}>
                <span className="row-text">
                  <b>{toolLabel(tool.id)}</b>
                  <small>{tool.reason}</small>
                </span>
              </div>
            ))}
          </div>
        </Collapsible>
      )}
      {Boolean(server.unreviewed?.length) && (
        <p className="muted">
          This connector also offers {server.unreviewed!.length} tool{server.unreviewed!.length === 1 ? '' : 's'} Mu3Lab
          has not reviewed yet; they stay unavailable: {server.unreviewed!.join(', ')}.
        </p>
      )}
    </div>
  );
}
