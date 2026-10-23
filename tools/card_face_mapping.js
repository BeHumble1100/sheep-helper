(() => {
  const game = window.frames[1];
  const scene = game.cc.director.getScene();
  const faces = [];
  const walk = (node) => {
    for (const component of node._components || []) {
      if (!component.cardDataObj) continue;
      const sprite = component.blockImg?.getComponent(game.cc.Sprite);
      const loader = component.blockImg?.getComponent("spriteFrameRuntimeLoader");
      faces.push({
        id: component.cardDataObj.id,
        type: component.cardDataObj.type,
        cardId: component.cardDataObj.cardId,
        imagePath: sprite?.pp_url ?? loader?.spriteFramePath ?? null,
        spriteName: sprite?.spriteFrame?.name ?? null,
        textureName: sprite?.spriteFrame?.texture?.name ?? null,
      });
    }
    for (const child of node.children || []) walk(child);
  };
  walk(scene);
  return faces;
})()
