import { GridStackEngine, type GridStackMoveOpts, type GridStackNode } from 'gridstack';

type FixCollisions = (
  this: GridStackEngine,
  node: GridStackNode,
  nn?: GridStackNode,
  collide?: GridStackNode,
  opt?: GridStackMoveOpts,
) => boolean;

/** A node's state during a drag: internal to gridstack, so not in its published types. */
type Dragged = GridStackNode & { _moving?: boolean; _isExternal?: boolean };

// gridstack's own collision step: internal, so not in its published types. Recheck on each gridstack update.
const fixCollisions = (GridStackEngine.prototype as unknown as { _fixCollisions: FixCollisions })._fixCollisions;

/**
 * gridstack's engine, swapping in free placement too. Out of the box gridstack swaps a dragged widget
 * with the one it lands on (same size, or lined up in a row or column) only when widgets pack to the
 * top; with gaps allowed it pushes the other widget down instead. Here a swap is tried first in both,
 * and pushing is what happens when a swap does not fit, as on Homarr's boards.
 */
export class SwappingEngine extends GridStackEngine {
  protected _fixCollisions(
    node: GridStackNode,
    nn: GridStackNode = node,
    collide?: GridStackNode,
    opt: GridStackMoveOpts = {},
  ): boolean {
    const dragged = node as Dragged;
    if (this.mode === 'float' && dragged._moving && !dragged._isExternal && !opt.nested) {
      const hit = collide ?? this.collide(node, nn);
      if (hit && this.swap(node, hit)) return true;
    }
    return fixCollisions.call(this, node, nn, collide, opt);
  }

  /**
   * gridstack remembers each column count's layout so a grid sized back up returns to it. A group's
   * grid changes columns whenever the group is resized, and its widgets keep their own places and sizes
   * then (layout 'none'); a remembered layout would put them back where they were before they moved.
   */
  cacheLayout(): GridStackEngine {
    return this;
  }
}
