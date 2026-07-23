import {
  MessageScroller as MessageScrollerPrimitive,
  useMessageScroller,
  useMessageScrollerScrollable,
  useMessageScrollerVisibility,
} from '@shadcn/react/message-scroller';
import { ArrowDown } from 'lucide-react';
import type { ComponentProps } from 'react';

import { cn } from '../../lib/utils';
import { Button } from './button';

function MessageScrollerProvider(props: ComponentProps<typeof MessageScrollerPrimitive.Provider>) {
  return <MessageScrollerPrimitive.Provider {...props} />;
}

function MessageScroller({ className, ...props }: ComponentProps<typeof MessageScrollerPrimitive.Root>) {
  return (
    <MessageScrollerPrimitive.Root
      className={cn('group/message-scroller relative flex size-full min-h-0 flex-col overflow-hidden', className)}
      data-slot="message-scroller"
      {...props}
    />
  );
}

function MessageScrollerViewport({ className, ...props }: ComponentProps<typeof MessageScrollerPrimitive.Viewport>) {
  return (
    <MessageScrollerPrimitive.Viewport
      className={cn('size-full min-h-0 min-w-0 overflow-y-auto overscroll-contain', className)}
      data-slot="message-scroller-viewport"
      {...props}
    />
  );
}

function MessageScrollerContent({ className, ...props }: ComponentProps<typeof MessageScrollerPrimitive.Content>) {
  return (
    <MessageScrollerPrimitive.Content
      className={cn('flex h-max min-h-full flex-col', className)}
      data-slot="message-scroller-content"
      {...props}
    />
  );
}

function MessageScrollerItem({
  className,
  scrollAnchor = false,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Item>) {
  return (
    <MessageScrollerPrimitive.Item
      className={cn('min-w-0 shrink-0 [contain-intrinsic-size:auto_10rem] [content-visibility:auto]', className)}
      data-slot="message-scroller-item"
      scrollAnchor={scrollAnchor}
      {...props}
    />
  );
}

function MessageScrollerButton({
  className,
  direction = 'end',
  children,
  ...props
}: ComponentProps<typeof MessageScrollerPrimitive.Button>) {
  return (
    <MessageScrollerPrimitive.Button
      className={cn(
        'absolute left-1/2 -translate-x-1/2 border border-slate-200 bg-white text-slate-500 shadow-sm transition-[translate,scale,opacity] duration-200 hover:bg-slate-50 hover:text-slate-700',
        'data-[active=false]:pointer-events-none data-[active=false]:scale-95 data-[active=false]:opacity-0 data-[active=false]:duration-400 data-[active=false]:ease-[cubic-bezier(0.7,0,0.84,0)]',
        'data-[active=true]:translate-y-0 data-[active=true]:scale-100 data-[active=true]:opacity-100 data-[active=true]:ease-[cubic-bezier(0.23,1,0.32,1)]',
        'data-[direction=end]:bottom-3 data-[direction=end]:data-[active=false]:translate-y-full',
        'data-[direction=start]:top-3 data-[direction=start]:data-[active=false]:-translate-y-full data-[direction=start]:[&_svg]:rotate-180',
        className
      )}
      data-direction={direction}
      data-slot="message-scroller-button"
      direction={direction}
      render={<Button size="icon" variant="outline" />}
      {...props}
    >
      {children ?? (
        <>
          <ArrowDown className="h-3.5 w-3.5" strokeWidth={1.75} />
          <span className="sr-only">{direction === 'end' ? '滚动到最新消息' : '滚动到顶部'}</span>
        </>
      )}
    </MessageScrollerPrimitive.Button>
  );
}

export {
  MessageScroller,
  MessageScrollerButton,
  MessageScrollerContent,
  MessageScrollerItem,
  MessageScrollerProvider,
  MessageScrollerViewport,
  useMessageScroller,
  useMessageScrollerScrollable,
  useMessageScrollerVisibility,
};
