(() => {
  const game = window.frames[1];
  const scene = game.cc.director.getScene();
  const found = { gameLayer: [], chessboard: [], cards: [] };
  const primitiveFields = (object) => Object.fromEntries(
    Object.entries(object || {}).filter(([, value]) =>
      value === null || ["string", "number", "boolean"].includes(typeof value),
    ),
  );
  const walk = (node) => {
    for (const component of node._components || []) {
      const className = component.__classname__ || component.constructor?.name;
      if (className === "gameLayer") {
        found.gameLayer.push({
          node: node.name,
          mapSeed: component.serverMapPlayInfo?.map_seed,
          levelDataFields: primitiveFields(component.levelDataArray?.[0]),
          levelCount: component.levelDataArray?.length,
          playLevelNum: component.playLevelNum,
        });
      }
      if (className === "chessboard") {
        found.chessboard.push({
          node: node.name,
          levelFields: primitiveFields(component.nowLevelData),
          blockTypeCount: component.blockTypeArr?.length,
          blockObjectLayerKeys: Object.keys(component.nowLevelBlockObj || {}),
        });
      }
      if (component.cardDataObj) {
        found.cards.push({ node: node.name, data: primitiveFields(component.cardDataObj) });
      }
    }
    for (const child of node.children || []) walk(child);
  };
  walk(scene);
  return found;
})()
