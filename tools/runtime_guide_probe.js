(() => {
  const frame = window.frames[1];
  const cc = frame?.cc;
  const scene = cc?.director?.getScene?.();
  if (!scene) return { ready: false, reason: "scene-unavailable" };
  const canvas = frame.document.querySelector("canvas");
  const cards = [];
  const walk = (node) => {
    const component = (node._components || []).find((item) => item.cardDataObj);
    if (component) {
      const data = component.cardDataObj;
      const point = node.convertToWorldSpaceAR(cc.v2(0, 0));
      cards.push({
        id: data.id,
        type: data.type,
        active: node.activeInHierarchy,
        x: point.x,
        y: point.y,
        width: node.width,
        height: node.height,
      });
    }
    for (const child of node.children || []) walk(child);
  };
  walk(scene);
  const rect = canvas?.getBoundingClientRect();
  return {
    ready: true,
    scene: scene.name,
    visible: cc.view.getVisibleSize(),
    frame: cc.view.getFrameSize(),
    canvas: rect ? { x: rect.x, y: rect.y, width: rect.width, height: rect.height } : null,
    total_cards: cards.length,
    cards: cards.slice(0, 12),
  };
})()
