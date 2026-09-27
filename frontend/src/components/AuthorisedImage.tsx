import { useEffect, useState } from "react";

import { accessToken } from "@/auth/oidc";

/**
 * An image fetched with the sign-in token. The API authorises every image, so a plain
 * `<img src>` would carry no token and be refused.
 */
export function AuthorisedImage({
  path,
  alt = "",
  className = "",
}: {
  path: string;
  alt?: string;
  className?: string;
}) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;

    async function load() {
      const token = await accessToken();
      const response = await fetch(new URL(path, window.location.origin), {
        headers: token ? { authorization: `Bearer ${token}` } : {},
      });
      if (!response.ok || cancelled) return;
      objectUrl = URL.createObjectURL(await response.blob());
      if (cancelled) URL.revokeObjectURL(objectUrl);
      else setUrl(objectUrl);
    }

    load().catch(() => {
      // Without the image the surrounding row still says what it is.
    });
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [path]);

  return url ? (
    <img src={url} alt={alt} className={className} />
  ) : (
    <div className={className} aria-hidden="true" />
  );
}
