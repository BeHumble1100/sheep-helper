(() => {
  const scene = window.frames[1].cc.director.getScene();
  const cards = [];
  const walk = (node) => {
    const cardComponent = (node._components || []).find((component) => component.cardDataObj);
    if (cardComponent) {
      const describe = (entry) => ({
        name: entry.name,
        components: (entry._components || []).map((component) => ({
          className: component.__classname__ || component.constructor?.name,
          spriteFrame: component.spriteFrame ? {
            name: component.spriteFrame.name,
            texture: component.spriteFrame.texture?.name,
          } : null,
          keys: Object.keys(component).filter((key) => !key.startsWith("_")).slice(0, 35),
        })),
        children: (entry.children || []).map(describe),
      });
      const sprite = cardComponent.blockImg?.getComponent(game.cc.Sprite);
      const loader = cardComponent.blockImg?.getComponent("spriteFrameRuntimeLoader");
      cards.push({
        data: cardComponent.cardDataObj,
        image: {
          ppUrl: sprite?.pp_url,
          spriteName: sprite?.spriteFrame?.name,
          textureName: sprite?.spriteFrame?.texture?.name,
          loaderPath: loader?.spriteFramePath,
        },
        componentKeys: Object.keys(cardComponent).filter((key) => !key.startsWith("_")).slice(0, 80),
        node: describe(node),
      });
    }
    for (const child of node.children || []) walk(child);
  };
  walk(scene);
  return cards;
})()
