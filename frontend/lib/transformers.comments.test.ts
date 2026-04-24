import { describe, expect, it } from 'vitest';

import { transformNoteCommentFromAPI, transformUserNotificationFromAPI } from './transformers';

describe('comment transformers', () => {
  it('maps nested note comment payloads into camelCase thread data', () => {
    const comment = transformNoteCommentFromAPI({
      id: 'comment-1',
      note_id: 'note-1',
      author_user_id: 'user-1',
      depth: 0,
      body: 'Hello @sam',
      is_resolved: false,
      created_at: '2026-04-21T10:00:00Z',
      author: { id: 'user-1', email: 'owner@example.com', name: 'Owner' },
      mentions: [
        {
          id: 'mention-1',
          comment_id: 'comment-1',
          mentioned_user_id: 'user-2',
          mention_token: 'sam',
          start_offset: 6,
          end_offset: 10,
          user: { id: 'user-2', email: 'sam@example.com', name: 'Sam Dev' },
        },
      ],
      reactions: [
        {
          emoji: 'thumbs_up',
          emoji_value: '👍',
          count: 2,
          reacted_by_current_user: true,
        },
      ],
      replies: [
        {
          id: 'comment-2',
          note_id: 'note-1',
          author_user_id: 'user-2',
          parent_comment_id: 'comment-1',
          depth: 1,
          body: 'Reply',
          is_resolved: true,
          created_at: '2026-04-21T10:05:00Z',
          author: { id: 'user-2', email: 'sam@example.com', name: 'Sam Dev' },
          mentions: [],
          reactions: [],
          replies: [],
        },
      ],
    });

    expect(comment.noteId).toBe('note-1');
    expect(comment.author?.name).toBe('Owner');
    expect(comment.mentions[0].mentionToken).toBe('sam');
    expect(comment.reactions[0].emojiValue).toBe('👍');
    expect(comment.replies[0].parentCommentId).toBe('comment-1');
    expect(comment.replies[0].isResolved).toBe(true);
  });

  it('maps durable notification payloads into frontend notification objects', () => {
    const notification = transformUserNotificationFromAPI({
      id: 'notification-1',
      user_id: 'user-2',
      actor_user_id: 'user-1',
      note_id: 'note-1',
      comment_id: 'comment-1',
      notification_type: 'comment_reply',
      is_read: false,
      created_at: '2026-04-21T11:00:00Z',
      payload: {
        note_title: 'Planning note',
        comment_excerpt: 'Following up here',
      },
      actor: {
        id: 'user-1',
        email: 'owner@example.com',
        name: 'Owner',
      },
    });

    expect(notification.notificationType).toBe('comment_reply');
    expect(notification.actor?.email).toBe('owner@example.com');
    expect(notification.payload.note_title).toBe('Planning note');
    expect(notification.isRead).toBe(false);
  });
});
