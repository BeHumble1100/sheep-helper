(() => {
  const game = window.frames[1];
  const cc = game.cc;
  const scene = cc?.director?.getScene?.();
  const nodes = [];
  const walk = (node, depth) => {
    if (!node || depth > 7 || nodes.length > 300) return;
    nodes.push({
      depth,
      name: node.name,
      active: node.active,
      children: node.children?.length ?? 0,
      components: (node._components || []).map((component) => ({
        name: component.name,
        className: component.__classname__ || component.constructor?.name,
        keys: Object.keys(component).filter((key) => !key.startsWith("_")).slice(0, 50),
      })),
    });
    for (const child of node.children || []) walk(child, depth + 1);
  };
  walk(scene, 0);
  return { scene: scene?.name, nodes };
})()
