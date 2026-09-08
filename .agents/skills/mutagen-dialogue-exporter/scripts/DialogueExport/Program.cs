using System.Text.Json;
using Mutagen.Bethesda;
using Mutagen.Bethesda.Plugins;
using Mutagen.Bethesda.Plugins.Cache;
using Mutagen.Bethesda.Plugins.Records;
using Mutagen.Bethesda.Skyrim;
using Mutagen.Bethesda.Strings;
using Noggog;

// Export DIAL -> INFO structure from a Skyrim plugin via Mutagen overlays.
// Usage: DialogueExport <plugin> <output.json> <dataDir>
var plugin = args.Length > 0 ? args[0] : @"mods/Druadach.esm/Druadach.esm";
var output = args.Length > 1 ? args[1] : @".work/Druadach-mutagen-dialogue.json";
var dataDir = args.Length > 2
    ? args[2]
    : @"D:\SteamLibrary\steamapps\common\Skyrim Special Edition\Data";

var sw = System.Diagnostics.Stopwatch.StartNew();
string S(ITranslatedStringGetter? t) => t == null ? "" : t.String ?? "";
string? FK(FormKey key) => key.IsNull ? null : key.ToString();
string? LinkFk<T>(IFormLinkGetter<T>? link) where T : class, IMajorRecordGetter
    => link == null ? null : FK(link.FormKey);

var modPath = new ModPath(ModKey.FromFileName(Path.GetFileName(plugin)), new FilePath(plugin));

// Target plugin as overlay (memory-mapped parse, no full deserialization).
using var overlay = SkyrimMod.CreateFromBinaryOverlay(modPath, SkyrimRelease.SkyrimSE);
Console.WriteLine($"[{sw.ElapsedMilliseconds}ms] target parsed, {overlay.DialogTopics.Count()} topics in file");

// Masters as overlays in declared order so FormKey links resolve cross-mod.
var masters = new List<ISkyrimModGetter>();
foreach (var masterLink in overlay.MasterReferences)
{
    var mKey = masterLink.Master;
    var mFull = Path.Combine(dataDir, mKey.FileName);
    if (!File.Exists(mFull))
    {
        Console.WriteLine($"WARNING missing master: {mFull}");
        continue;
    }
    masters.Add(SkyrimMod.CreateFromBinaryOverlay(new ModPath(mKey, new FilePath(mFull)), SkyrimRelease.SkyrimSE));
    Console.WriteLine($"[{sw.ElapsedMilliseconds}ms] master overlay: {mKey}");
}

var loadOrderMods = masters.Append(overlay).ToList();
ILinkCache cache = loadOrderMods.ToImmutableLinkCache<ISkyrimMod, ISkyrimModGetter>();
Console.WriteLine($"[{sw.ElapsedMilliseconds}ms] link cache built");

string? NpcName(IFormLinkGetter<INpcGetter>? link)
{
    if (link == null) return null;
    var getter = link.TryResolve(cache);
    return getter is INpcGetter npc ? S(npc.Name) : null;
}

string? QuestEdid(IFormLinkGetter<IQuestGetter>? link)
{
    if (link == null) return null;
    var getter = link.TryResolve(cache);
    return getter is IQuestGetter q ? q.EditorID : null;
}

var topicsOut = new List<object>();
int infoTotal = 0;
foreach (var dial in overlay.DialogTopics)
{
    var infos = new List<object>();
    foreach (var info in dial.Responses)
    {
        infoTotal++;
        infos.Add(new
        {
            formKey = FK(info.FormKey),
            edid = info.EditorID,
            prompt = S(info.Prompt),
            speaker = LinkFk(info.Speaker),
            speakerName = NpcName(info.Speaker),
            prev = LinkFk(info.PreviousDialog),
            responses = info.Responses.Select(r => S(r.Text)).ToList(),
            conditions = info.Conditions.Select(c => new
            {
                kind = c.Data.GetType().Name,
                runOn = c.Data.RunOnType.ToString(),
                reference = FK(c.Data.Reference.FormKey),
            }).ToList(),
        });
    }
    topicsOut.Add(new
    {
        formKey = FK(dial.FormKey),
        edid = dial.EditorID,
        topic = S(dial.Name),
        category = dial.Category.ToString(),
        subtype = dial.Subtype.ToString(),
        quest = LinkFk(dial.Quest),
        questEdid = QuestEdid(dial.Quest),
        branch = LinkFk(dial.Branch),
        infos,
    });
}

var doc = new
{
    target = modPath.ModKey.ToString(),
    generatedBy = "mutagen-dialogue-export",
    elapsedMs = sw.ElapsedMilliseconds,
    topicCount = topicsOut.Count,
    infoCount = infoTotal,
    topics = topicsOut,
};
Directory.CreateDirectory(Path.GetDirectoryName(output)!);
File.WriteAllText(output, JsonSerializer.Serialize(doc, new JsonSerializerOptions
{
    WriteIndented = true,
    Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
}));
Console.WriteLine($"[{sw.ElapsedMilliseconds}ms] wrote {output}: {topicsOut.Count} DIAL / {infoTotal} INFO");
