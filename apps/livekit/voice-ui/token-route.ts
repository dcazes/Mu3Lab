// Mu3Lab's room-token route, replacing agent-starter-react's app/api/token/route.ts.
//
// Authentik admits the visitor before this page loads, and Caddy passes their verified email and
// name (replacing anything the browser sent). The token names them, so the voice agent acts as
// that person with their own app tools (ctl/voice_tools.py). Without a sign-in there is no token.
import { NextResponse } from 'next/server';
import {
  AccessToken,
  type AccessTokenOptions,
  RoomConfiguration,
  type VideoGrant,
} from 'livekit-server-sdk';

type ConnectionDetails = {
  serverUrl: string;
  roomName: string;
  participantName: string;
  participantToken: string;
};

const API_KEY = process.env.LIVEKIT_API_KEY;
const API_SECRET = process.env.LIVEKIT_API_SECRET;
const LIVEKIT_URL = process.env.LIVEKIT_URL;

// don't cache the results
export const revalidate = 0;

export async function POST(req: Request) {
  const email = (req.headers.get('x-authentik-email') ?? '').trim().toLowerCase();
  if (!email) {
    return new NextResponse('Sign in to Mu3Lab to talk to the assistant.', { status: 401 });
  }
  const participantName = (req.headers.get('x-authentik-name') ?? '').trim() || email.split('@')[0];
  try {
    if (LIVEKIT_URL === undefined || API_KEY === undefined || API_SECRET === undefined) {
      throw new Error('LIVEKIT_URL, LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set');
    }

    // The page asks for the agent by name in the room configuration.
    const body = await req.json();
    const roomConfig = body?.room_config
      ? RoomConfiguration.fromJson(body.room_config, { ignoreUnknownFields: true })
      : new RoomConfiguration();

    // One new room per call; the identity is the person, so the agent knows whose tools to use.
    const roomName = `voice_${crypto.randomUUID()}`;
    const participantToken = await createParticipantToken(
      { identity: email, name: participantName, attributes: { 'mu3lab.email': email } },
      roomName,
      roomConfig
    );

    const data: ConnectionDetails = {
      serverUrl: LIVEKIT_URL,
      roomName,
      participantName,
      participantToken,
    };
    return NextResponse.json(data, { headers: new Headers({ 'Cache-Control': 'no-store' }) });
  } catch (error) {
    console.error(error);
    return new NextResponse(error instanceof Error ? error.message : 'Could not start the call.', {
      status: 500,
    });
  }
}

function createParticipantToken(
  userInfo: AccessTokenOptions,
  roomName: string,
  roomConfig: RoomConfiguration
): Promise<string> {
  const at = new AccessToken(API_KEY, API_SECRET, {
    ...userInfo,
    ttl: '15m',
  });
  const grant: VideoGrant = {
    room: roomName,
    roomJoin: true,
    canPublish: true,
    canPublishData: true,
    canSubscribe: true,
    // The person's name and email are set here, never by the browser.
    canUpdateOwnMetadata: false,
  };
  at.addGrant(grant);
  at.roomConfig = roomConfig;
  return at.toJwt();
}
