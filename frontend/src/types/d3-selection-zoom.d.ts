declare module 'd3-selection' {
  export const select: any;
}

declare module 'd3-zoom' {
  export type ZoomTransform = {
    x: number;
    y: number;
    k: number;
  };

  export type ZoomBehavior<TElement, Datum> = any;

  export const zoomIdentity: ZoomTransform & {
    translate: (x: number, y: number) => ZoomTransform & {
      scale: (k: number) => ZoomTransform;
    };
    scale: (k: number) => ZoomTransform;
  };

  export const zoom: any;
}
